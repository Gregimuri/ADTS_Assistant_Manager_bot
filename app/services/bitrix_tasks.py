from __future__ import annotations

import logging
import re
from typing import Any

from app.config import Settings
from app.services.bitrix_rest import bitrix_call

logger = logging.getLogger(__name__)


class BitrixTasksError(RuntimeError):
    """Не удалось загрузить задачи из Bitrix."""


class BitrixTasksClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._store_task_cache: dict[tuple[str, bool], str] = {}

    async def find_task_id_for_store(self, query: str, *, emm_only: bool = False) -> str:
        """Ищет задачу Bitrix по названию ТТ (лист ТО больше не хранит id)."""
        key = (query.strip().casefold(), emm_only)
        if key in self._store_task_cache:
            return self._store_task_cache[key]
        cleaned = query.strip()
        if not cleaned or not self._settings.bitrix_webhook_url.strip():
            self._store_task_cache[key] = ""
            return ""

        search_terms: list[str] = [cleaned]
        parts = cleaned.split()
        if len(parts) > 2:
            search_terms.append(" ".join(parts[:2]))
        if parts:
            search_terms.append(parts[0])

        seen_terms: set[str] = set()
        task_id = ""
        for term in search_terms:
            marker = term.casefold()
            if marker in seen_terms:
                continue
            seen_terms.add(marker)
            try:
                raw_tasks = await self._search_tasks_by_title(term)
            except BitrixTasksError:
                logger.exception("Bitrix task lookup failed for %r", term)
                break
            picked = _pick_store_task(raw_tasks, cleaned, emm_only=emm_only)
            if picked:
                task_id = picked
                break

        self._store_task_cache[key] = task_id
        return task_id

    async def _search_tasks_by_title(self, term: str) -> list[dict[str, Any]]:
        try:
            payload = await bitrix_call(
                self._settings,
                "tasks.task.list",
                {
                    "select": ["ID", "TITLE", "DESCRIPTION"],
                    "filter": {"%TITLE": term},
                    "start": 0,
                },
                timeout_seconds=60,
            )
        except RuntimeError as exc:
            raise BitrixTasksError(str(exc)) from exc
        if isinstance(payload, dict):
            batch = payload.get("tasks") or []
            return [item for item in batch if isinstance(item, dict)]
        return []


def _pick_store_task(
    raw_tasks: list[dict[str, Any]],
    store_query: str,
    *,
    emm_only: bool,
) -> str:
    for raw in raw_tasks:
        title = str(raw.get("title") or raw.get("TITLE") or "")
        description = str(raw.get("description") or raw.get("DESCRIPTION") or "")
        if emm_only and "емм" not in f"{title} {description}".casefold():
            continue
        if _store_name_in_title(title, store_query):
            return str(raw.get("id") or raw.get("ID") or "").strip()
    for raw in raw_tasks:
        title = str(raw.get("title") or raw.get("TITLE") or "")
        description = str(raw.get("description") or raw.get("DESCRIPTION") or "")
        if emm_only and "емм" not in f"{title} {description}".casefold():
            continue
        if store_query.casefold() in title.casefold():
            return str(raw.get("id") or raw.get("ID") or "").strip()
    return ""


def _store_name_in_title(title: str, store_query: str) -> bool:
    pattern = rf"(?<!\w){re.escape(store_query)}(?!\w)"
    return bool(re.search(pattern, title, flags=re.IGNORECASE))
