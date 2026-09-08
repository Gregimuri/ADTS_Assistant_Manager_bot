from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)


class ReportStorage:
    """Локальное хранилище утренних планов и снимков сборки."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()

    async def save_exit_plan(self, day: date, counts: dict[str, int]) -> None:
        async with self._lock:
            data = self._read()
            plans = data.setdefault("exit_plans", {})
            day_key = day.isoformat()
            # Утренний план фиксируется один раз: повторный пересчёт не перезаписывает.
            if day_key in plans and isinstance(plans[day_key], dict) and plans[day_key]:
                logger.info(
                    "Exit plan for %s already fixed (%s projects), skip overwrite",
                    day_key,
                    len(plans[day_key]),
                )
                return
            plans[day_key] = {project: int(value) for project, value in counts.items()}
            self._write(data)
            logger.info("Fixed exit plan for %s (%s projects)", day_key, len(counts))

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
            day_key = day.isoformat()
            if day_key in snapshots and isinstance(snapshots[day_key], dict):
                logger.info("Assembly snapshot for %s already fixed, skip overwrite", day_key)
                return
            snapshots[day_key] = {
                "open_count": int(open_count),
                "task_ids": [str(task_id) for task_id in task_ids],
            }
            self._write(data)
            logger.info(
                "Fixed assembly snapshot for %s (%s open tasks)",
                day_key,
                open_count,
            )

    def get_assembly_snapshot(self, day: date) -> dict | None:
        data = self._read()
        snapshot = data.get("assembly_snapshots", {}).get(day.isoformat())
        if not isinstance(snapshot, dict):
            return None
        return snapshot

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
