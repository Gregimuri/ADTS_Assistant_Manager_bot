from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import aiohttp

from app.config import Settings

logger = logging.getLogger(__name__)

_STORE_TASK_TITLE = "[assistant-manager] reports"
_FLUSH_DELAY_SECONDS = 1.0


class ReportStorage:
    """Локальный JSON + копия в Bitrix, чтобы утренний план переживал деплой."""

    def __init__(self, path: Path, settings: Settings) -> None:
        self._path = path
        self._settings = settings
        self._lock = asyncio.Lock()
        self._remote_task_id: str | None = None
        self._flush_task: asyncio.Task[None] | None = None
        self._dirty = False

    async def hydrate(self) -> None:
        """Подтягивает сохранённые планы из Bitrix при старте."""
        local = self._read()
        remote = await self._load_remote()
        if remote is None:
            if _has_payload(local):
                self._dirty = True
                self._schedule_flush()
            return

        merged = _merge_reports(local, remote)
        async with self._lock:
            self._write(merged)
        logger.info(
            "Hydrated report storage (%s exit plan days, %s assembly snapshots)",
            len(merged.get("exit_plans", {})),
            len(merged.get("assembly_snapshots", {})),
        )
        if merged != remote:
            self._dirty = True
            self._schedule_flush()

    async def save_exit_plan(self, day: date, counts: dict[str, int]) -> None:
        async with self._lock:
            data = self._read()
            plans = data.setdefault("exit_plans", {})
            plans[day.isoformat()] = {project: int(value) for project, value in counts.items()}
            self._write(data)
            self._dirty = True
            logger.info("Saved exit plan for %s (%s projects)", day.isoformat(), len(counts))
        await self.flush()

    def get_exit_plan(self, day: date, project: str) -> int | None:
        data = self._read()
        day_data = data.get("exit_plans", {}).get(day.isoformat(), {})
        if not isinstance(day_data, dict) or project not in day_data:
            return None
        try:
            return int(day_data[project])
        except (TypeError, ValueError):
            return None

    async def save_assembly_snapshot(
        self,
        day: date,
        *,
        open_count: int,
        task_ids: list[str],
    ) -> None:
        async with self._lock:
            data = self._read()
            snapshots = data.setdefault("assembly_snapshots", {})
            snapshots[day.isoformat()] = {
                "open_count": int(open_count),
                "task_ids": [str(task_id) for task_id in task_ids],
            }
            self._write(data)
            self._dirty = True
            logger.info(
                "Saved assembly snapshot for %s (%s open tasks)",
                day.isoformat(),
                open_count,
            )
        await self.flush()

    def get_assembly_snapshot(self, day: date) -> dict | None:
        data = self._read()
        snapshot = data.get("assembly_snapshots", {}).get(day.isoformat())
        if not isinstance(snapshot, dict):
            return None
        return snapshot

    async def flush(self) -> None:
        async with self._lock:
            if not self._dirty:
                return
            data = self._read()
            self._dirty = False
        await self._save_remote(data)

    def _schedule_flush(self) -> None:
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()

        async def _delayed() -> None:
            try:
                await asyncio.sleep(_FLUSH_DELAY_SECONDS)
                await self.flush()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Failed to flush report storage to Bitrix")

        self._flush_task = asyncio.create_task(_delayed())

    def _read(self) -> dict:
        if not self._path.exists():
            return {"exit_plans": {}, "assembly_snapshots": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("Failed to read report storage from %s", self._path)
            return {"exit_plans": {}, "assembly_snapshots": {}}
        if not isinstance(data, dict):
            return {"exit_plans": {}, "assembly_snapshots": {}}
        data.setdefault("exit_plans", {})
        data.setdefault("assembly_snapshots", {})
        return data

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(self._path)

    async def _load_remote(self) -> dict | None:
        if not self._settings.bitrix_webhook_url.strip():
            return None
        try:
            task = await self._find_store_task()
            if task is None:
                return None
            self._remote_task_id = str(task.get("id") or task.get("ID") or "") or None
            raw = str(task.get("description") or task.get("DESCRIPTION") or "").strip()
            if not raw:
                return {"exit_plans": {}, "assembly_snapshots": {}}
            raw = raw.replace("[code]", "").replace("[/code]", "").strip()
            data = json.loads(raw)
            if not isinstance(data, dict):
                return {"exit_plans": {}, "assembly_snapshots": {}}
            data.setdefault("exit_plans", {})
            data.setdefault("assembly_snapshots", {})
            return data
        except Exception:
            logger.exception("Failed to load report storage from Bitrix")
            return None

    async def _save_remote(self, data: dict) -> None:
        if not self._settings.bitrix_webhook_url.strip():
            return
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        try:
            task_id = self._remote_task_id
            if not task_id:
                task = await self._find_store_task()
                if task is not None:
                    task_id = str(task.get("id") or task.get("ID") or "") or None
                    self._remote_task_id = task_id
            if task_id:
                await self._bitrix_call(
                    "tasks.task.update",
                    {
                        "taskId": task_id,
                        "fields": {
                            "DESCRIPTION": payload,
                            "DESCRIPTION_IN_BBCODE": "N",
                        },
                    },
                )
            else:
                result = await self._bitrix_call(
                    "tasks.task.add",
                    {
                        "fields": {
                            "TITLE": _STORE_TASK_TITLE,
                            "DESCRIPTION": payload,
                            "DESCRIPTION_IN_BBCODE": "N",
                            "RESPONSIBLE_ID": self._settings.bitrix_assembly_responsible_id,
                        },
                    },
                )
                task = result.get("task") if isinstance(result, dict) else None
                if isinstance(task, dict):
                    self._remote_task_id = str(task.get("id") or task.get("ID") or "") or None
            logger.info("Saved report storage to Bitrix task_id=%s", self._remote_task_id)
        except Exception:
            logger.exception("Failed to save report storage to Bitrix")
            async with self._lock:
                self._dirty = True

    async def _find_store_task(self) -> dict[str, Any] | None:
        result = await self._bitrix_call(
            "tasks.task.list",
            {
                "select": ["ID", "TITLE", "DESCRIPTION"],
                "filter": {"TITLE": _STORE_TASK_TITLE},
                "start": 0,
            },
        )
        tasks: list[Any] = []
        if isinstance(result, dict):
            tasks = result.get("tasks") or []
        elif isinstance(result, list):
            tasks = result
        for item in tasks:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("TITLE") or "")
            if title == _STORE_TASK_TITLE:
                return item
        return None

    async def _bitrix_call(self, method: str, params: dict[str, Any]) -> Any:
        base = self._settings.bitrix_webhook_url.rstrip("/") + "/"
        url = urljoin(base, method)
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=_flatten_params(params)) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
        if not isinstance(data, dict):
            raise RuntimeError("Bitrix returned unexpected payload")
        if data.get("error"):
            raise RuntimeError(f"Bitrix API: {data.get('error_description') or data['error']}")
        return data.get("result")


def _merge_reports(local: dict, remote: dict) -> dict:
    merged = {
        "exit_plans": {},
        "assembly_snapshots": {},
    }
    for key in ("exit_plans", "assembly_snapshots"):
        bucket: dict[str, Any] = {}
        remote_bucket = remote.get(key)
        local_bucket = local.get(key)
        if isinstance(remote_bucket, dict):
            bucket.update(remote_bucket)
        if isinstance(local_bucket, dict):
            bucket.update(local_bucket)
        merged[key] = bucket
    return merged


def _has_payload(data: dict) -> bool:
    plans = data.get("exit_plans")
    snapshots = data.get("assembly_snapshots")
    return bool(isinstance(plans, dict) and plans) or bool(
        isinstance(snapshots, dict) and snapshots
    )


def _flatten_params(params: dict[str, Any], prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in params.items():
        full_key = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, list):
            for index, item in enumerate(value):
                flat[f"{full_key}[{index}]"] = str(item)
        elif isinstance(value, dict):
            flat.update(_flatten_params(value, full_key))
        else:
            flat[full_key] = str(value)
    return flat
