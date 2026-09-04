from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)


class ReportStorage:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()

    async def save_exit_plan(self, day: date, counts: dict[str, int]) -> None:
        async with self._lock:
            data = self._read()
            plans = data.setdefault("exit_plans", {})
            plans[day.isoformat()] = {project: int(value) for project, value in counts.items()}
            self._write(data)
            logger.info("Saved exit plan for %s (%s projects)", day.isoformat(), len(counts))

    def get_exit_plan(self, day: date, project: str) -> int | None:
        data = self._read()
        day_data = data.get("exit_plans", {}).get(day.isoformat(), {})
        if project not in day_data:
            return None
        return int(day_data[project])

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
            logger.info(
                "Saved assembly snapshot for %s (%s open tasks)",
                day.isoformat(),
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
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("Failed to read report storage from %s", self._path)
            return {}

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
