from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ReportStorage:
    """Локальное хранилище очереди исполнителей ФО."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()

    async def next_fo_responsible_id(self, candidates: tuple[int, ...] | list[int]) -> int:
        """Круговая очередь исполнителей ФО: Андреевских → Корчагин → Абакумов → …"""
        pool = [int(user_id) for user_id in candidates if int(user_id) > 0]
        if not pool:
            raise ValueError("Список исполнителей ФО пуст.")
        async with self._lock:
            data = self._read()
            state = data.setdefault("fo_responsible_rr", {})
            try:
                index = int(state.get("next_index") or 0) % len(pool)
            except (TypeError, ValueError):
                index = 0
            user_id = pool[index]
            state["next_index"] = (index + 1) % len(pool)
            state["last_user_id"] = user_id
            try:
                self._write(data)
            except OSError:
                logger.exception(
                    "FO round-robin: не удалось сохранить очередь в %s",
                    self._path,
                )
            logger.info(
                "FO responsible round-robin -> %s (index %s/%s)",
                user_id,
                index,
                len(pool),
            )
            return user_id

    def _read(self) -> dict:
        if not self._path.exists():
            return {"fo_responsible_rr": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("Failed to read report storage from %s", self._path)
            return {"fo_responsible_rr": {}}
        if not isinstance(data, dict):
            return {"fo_responsible_rr": {}}
        data.setdefault("fo_responsible_rr", {})
        return data

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(self._path)
