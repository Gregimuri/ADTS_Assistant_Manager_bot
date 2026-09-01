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
