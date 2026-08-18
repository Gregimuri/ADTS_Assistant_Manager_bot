from __future__ import annotations

import asyncio
import csv
import io
import logging
import time
from dataclasses import dataclass

import aiohttp

from app.config import Settings

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (compatible; AssistantManagerBot/1.0; "
    "+https://github.com/assistant-manager-bot)"
)


@dataclass(frozen=True, slots=True)
class Player:
    object_number: str
    address: str
    name: str
    emm: str
    reflash: str
    cube: str
    do_nothing: str


class SheetsError(RuntimeError):
    """Не удалось загрузить таблицу."""


class SheetsClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._players: list[Player] | None = None
        self._loaded_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_players(self) -> list[Player]:
        async with self._lock:
            now = time.monotonic()
            ttl = self._settings.sheets_cache_ttl_seconds
            if self._players is not None and now - self._loaded_at < ttl:
                return self._players
            self._players = await self._fetch()
            self._loaded_at = now
            logger.info("Loaded %s players from Google Sheets", len(self._players))
            return self._players

    async def _fetch(self) -> list[Player]:
        urls = [
            (
                "https://docs.google.com/spreadsheets/d/"
                f"{self._settings.spreadsheet_id}/export?format=csv"
                f"&gid={self._settings.spreadsheet_gid}"
            ),
            (
                "https://docs.google.com/spreadsheets/d/"
                f"{self._settings.spreadsheet_id}/gviz/tq?tqx=out:csv"
                f"&gid={self._settings.spreadsheet_gid}"
            ),
        ]
        last_error: Exception | None = None
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for url in urls:
                try:
                    async with session.get(
                        url,
                        headers={"User-Agent": USER_AGENT},
                        allow_redirects=True,
                    ) as response:
                        response.raise_for_status()
                        text = await response.text()
                    players = _parse_csv(text)
                    if players:
                        return players
                    last_error = SheetsError("Таблица пуста или без ожидаемых колонок")
                except Exception as exc:  # noqa: BLE001 — пробуем запасной URL
                    last_error = exc
                    logger.warning("Failed to load sheet from %s: %s", url, exc)
        raise SheetsError(f"Не удалось загрузить таблицу: {last_error}") from last_error


def _parse_csv(text: str) -> list[Player]:
    text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []

    headers = list(reader.fieldnames)
    if any(len(name) > 80 for name in headers):
        logger.error("Sheet response is not a valid CSV header row")
        return []

    field_map = _map_fields(headers)
    required = ("object_number", "address", "name")
    if any(key not in field_map for key in required):
        logger.error("Unexpected sheet headers: %s", headers)
        return []

    players: list[Player] = []
    for row in reader:
        name = _cell(row, field_map["name"])
        object_number = _cell(row, field_map["object_number"])
        if not name or not object_number:
            continue
        players.append(
            Player(
                object_number=object_number,
                address=_cell(row, field_map["address"]),
                name=name,
                emm=_cell(row, field_map.get("emm")),
                reflash=_cell(row, field_map.get("reflash")),
                cube=_cell(row, field_map.get("cube")),
                do_nothing=_cell(row, field_map.get("do_nothing")),
            )
        )
    return players


def _map_fields(fieldnames: list[str]) -> dict[str, str]:
    aliases = {
        "object_number": ("objectnumber", "object number"),
        "address": ("адрес", "address"),
        "name": ("name", "имя", "название"),
        "emm": ("емм", "emm"),
        "reflash": ("перепрошить", "reflash"),
        "cube": ("обновить кубик", "кубик"),
        "do_nothing": ("ничего не делать",),
    }
    normalized = {name: _normalize_header(name) for name in fieldnames}
    mapping: dict[str, str] = {}
    for key, options in aliases.items():
        for original, norm in normalized.items():
            if norm in options:
                mapping[key] = original
                break
    return mapping


def _normalize_header(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").split())


def _cell(row: dict[str, str | None], key: str | None) -> str:
    if not key:
        return ""
    value = row.get(key)
    if value is None:
        return ""
    return str(value).strip()
