from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable
from typing import Any

from app.config import Settings
from app.services.bitrix_rest import bitrix_call
from app.services.catalog import Catalog

logger = logging.getLogger(__name__)


class FoManagerRegistry:
    """Сопоставление ФИО менеджера из таблицы с пользователем Bitrix."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache: dict[str, tuple[int, str]] = {}
        self._lock = asyncio.Lock()

    async def warm_up(self, catalog: Catalog, projects: Iterable[str]) -> None:
        names: set[str] = set()
        for project in projects:
            try:
                stores = await catalog.load_project_stores(project)
            except Exception:
                logger.exception("FO managers warm-up: failed to load project %s", project)
                continue
            for store in stores:
                cleaned = (store.manager or "").strip()
                if cleaned:
                    names.add(cleaned)
        logger.info("FO managers warm-up: %s unique names in FO projects", len(names))
        semaphore = asyncio.Semaphore(5)

        async def _warm(name: str) -> None:
            async with semaphore:
                await self.resolve(name)

        await asyncio.gather(*(_warm(name) for name in sorted(names)))
        logger.info("FO managers warm-up finished, resolved %s entries", len(self._cache))

    async def resolve(self, manager_name: str) -> tuple[int, str]:
        cleaned = (manager_name or "").strip()
        fallback_id = self._settings.bitrix_fo_fallback_creator_id
        fallback_name = "Титков Григорий"
        if not cleaned:
            return fallback_id, fallback_name

        key = _normalize_person(cleaned)
        async with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached

        user_id, full_name = await self._lookup_in_bitrix(cleaned, fallback_id, fallback_name)
        async with self._lock:
            self._cache[key] = (user_id, full_name)
        return user_id, full_name

    async def _lookup_in_bitrix(
        self,
        manager_name: str,
        fallback_id: int,
        fallback_name: str,
    ) -> tuple[int, str]:
        if not self._settings.bitrix_webhook_url.strip():
            return fallback_id, fallback_name
        try:
            user = await self._find_user_by_name(manager_name)
        except Exception:
            logger.exception("Bitrix user lookup failed for %r", manager_name)
            return fallback_id, fallback_name
        if user is None:
            logger.info("Manager %r not found in Bitrix, fallback to %s", manager_name, fallback_id)
            return fallback_id, fallback_name
        user_id = int(user.get("ID") or user.get("id") or 0)
        full_name = _format_user_name(user)
        if user_id <= 0:
            return fallback_id, fallback_name
        return user_id, full_name or manager_name

    async def _find_user_by_name(self, manager_name: str) -> dict[str, Any] | None:
        result = await bitrix_call(
            self._settings,
            "user.search",
            {"FILTER": {"FIND": manager_name}},
            timeout_seconds=60,
        )
        users = result if isinstance(result, list) else []
        if not users:
            parts = manager_name.split()
            if len(parts) >= 2:
                swapped = f"{parts[-1]} {' '.join(parts[:-1])}"
                result = await bitrix_call(
                    self._settings,
                    "user.search",
                    {"FILTER": {"FIND": swapped}},
                    timeout_seconds=60,
                )
                users = result if isinstance(result, list) else []
        for user in users:
            if not isinstance(user, dict):
                continue
            if user.get("ACTIVE") is False:
                continue
            full = _format_user_name(user)
            if _names_equivalent(full, manager_name) or _person_contains(full, manager_name):
                return user
        # Не берём «первый попавшийся» результат поиска — только явное совпадение ФИО.
        return None


def _format_user_name(user: dict[str, Any]) -> str:
    last = str(user.get("LAST_NAME") or user.get("lastName") or "").strip()
    first = str(user.get("NAME") or user.get("name") or "").strip()
    return " ".join(part for part in (last, first) if part).strip()


def _names_equivalent(left: str, right: str) -> bool:
    return _normalize_person(left) == _normalize_person(right)


def _normalize_person(value: str) -> str:
    parts = [part for part in re.split(r"\s+", value.strip().casefold()) if part]
    return " ".join(sorted(parts))


def _person_contains(full_name: str, query: str) -> bool:
    full_parts = set(_normalize_person(full_name).split())
    query_parts = set(_normalize_person(query).split())
    if not query_parts:
        return False
    return query_parts.issubset(full_parts)
