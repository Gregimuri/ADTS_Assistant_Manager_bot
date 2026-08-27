from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import quote

import aiohttp

from app.config import Settings

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (compatible; AssistantManagerBot/1.0; "
    "+https://github.com/assistant-manager-bot)"
)

_MONEY_RE = re.compile(r"[^\d,.\-]")


@dataclass(frozen=True, slots=True)
class Player:
    object_number: str
    address: str
    name: str
    emm: str
    reflash: str
    cube: str
    do_nothing: str


@dataclass(frozen=True, slots=True)
class ToVisit:
    name: str
    address: str
    work_type: str
    actual_cost: int
    extra_cost: int
    bitrix_task_id: str


@dataclass(frozen=True, slots=True)
class ProjectStore:
    project: str
    name: str
    region: str
    address: str
    manager: str
    codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DoStore:
    project: str
    name: str
    region: str
    address: str
    manager: str
    logistics: str
    expense_task: str
    order_date_raw: str
    acceptance: str


class SheetsError(RuntimeError):
    """Не удалось загрузить таблицу."""


class SheetsClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._players: list[Player] | None = None
        self._players_loaded_at: float = 0.0
        self._to_visits: list[ToVisit] | None = None
        self._to_loaded_at: float = 0.0
        self._project_names: list[str] | None = None
        self._project_names_loaded_at: float = 0.0
        self._project_stores: dict[str, list[ProjectStore]] = {}
        self._project_stores_loaded_at: dict[str, float] = {}
        self._do_stores: list[DoStore] | None = None
        self._do_loaded_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_players(self) -> list[Player]:
        async with self._lock:
            now = time.monotonic()
            ttl = self._settings.sheets_cache_ttl_seconds
            if self._players is not None and now - self._players_loaded_at < ttl:
                return self._players
            text = await self._fetch_csv(gid=self._settings.spreadsheet_gid)
            self._players = _parse_emm_csv(text)
            self._players_loaded_at = now
            logger.info("Loaded %s players from Google Sheets", len(self._players))
            return self._players

    async def get_to_visits(self) -> list[ToVisit]:
        async with self._lock:
            now = time.monotonic()
            ttl = self._settings.sheets_cache_ttl_seconds
            if self._to_visits is not None and now - self._to_loaded_at < ttl:
                return self._to_visits
            text = await self._fetch_csv(sheet=self._settings.to_sheet_name)
            self._to_visits = _parse_to_csv(text)
            self._to_loaded_at = now
            logger.info("Loaded %s TO visits from Google Sheets", len(self._to_visits))
            return self._to_visits

    async def get_project_names(self) -> list[str]:
        async with self._lock:
            now = time.monotonic()
            ttl = self._settings.sheets_cache_ttl_seconds
            if self._project_names is not None and now - self._project_names_loaded_at < ttl:
                return self._project_names
        text = await self._fetch_csv(sheet=self._settings.directory_sheet_name)
        names = _parse_directory_csv(text)
        async with self._lock:
            self._project_names = names
            self._project_names_loaded_at = time.monotonic()
            logger.info("Loaded %s projects from directory", len(names))
            return names

    async def get_project_stores(self, project: str) -> list[ProjectStore]:
        async with self._lock:
            now = time.monotonic()
            ttl = self._settings.sheets_cache_ttl_seconds
            loaded_at = self._project_stores_loaded_at.get(project)
            if (
                project in self._project_stores
                and loaded_at is not None
                and now - loaded_at < ttl
            ):
                return self._project_stores[project]
        text = await self._fetch_csv(sheet=project)
        stores = _parse_project_csv(text, project)
        async with self._lock:
            self._project_stores[project] = stores
            self._project_stores_loaded_at[project] = time.monotonic()
            logger.info("Loaded %s stores from project sheet %s", len(stores), project)
            return stores

    async def get_all_project_stores(self) -> list[ProjectStore]:
        names = await self.get_project_names()
        results = await asyncio.gather(
            *[self.get_project_stores(name) for name in names],
            return_exceptions=True,
        )
        stores: list[ProjectStore] = []
        for name, result in zip(names, results, strict=True):
            if isinstance(result, Exception):
                logger.warning("Failed to load project sheet %s: %s", name, result)
                continue
            stores.extend(result)
        return stores

    async def get_do_stores(self) -> list[DoStore]:
        async with self._lock:
            now = time.monotonic()
            ttl = self._settings.sheets_cache_ttl_seconds
            if self._do_stores is not None and now - self._do_loaded_at < ttl:
                return self._do_stores
        sheet = self._settings.do_sheet_name
        text = await self._fetch_csv(sheet=sheet)
        stores = _parse_do_csv(text, sheet)
        async with self._lock:
            self._do_stores = stores
            self._do_loaded_at = time.monotonic()
            logger.info("Loaded %s stores from DO sheet %s", len(stores), sheet)
            return stores

    async def _fetch_csv(self, *, gid: int | None = None, sheet: str | None = None) -> str:
        urls: list[str] = []
        base = f"https://docs.google.com/spreadsheets/d/{self._settings.spreadsheet_id}"
        if sheet:
            urls.append(f"{base}/gviz/tq?tqx=out:csv&sheet={quote(sheet)}")
        if gid is not None:
            urls.extend(
                [
                    f"{base}/export?format=csv&gid={gid}",
                    f"{base}/gviz/tq?tqx=out:csv&gid={gid}",
                ]
            )
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
                    if text.lstrip("\ufeff").startswith("<!"):
                        raise SheetsError("Получен HTML вместо CSV")
                    return text
                except Exception as exc:  # noqa: BLE001 — пробуем запасной URL
                    last_error = exc
                    logger.warning("Failed to load sheet from %s: %s", url, exc)
        raise SheetsError(f"Не удалось загрузить таблицу: {last_error}") from last_error


def parse_money(value: str) -> int:
    raw = (value or "").strip()
    if not raw:
        return 0
    cleaned = (
        raw.replace("\xa0", "")
        .replace("\u202f", "")
        .replace(" ", "")
        .replace("₽", "")
        .strip()
    )
    cleaned = _MONEY_RE.sub("", cleaned)
    if not cleaned or cleaned in {".", ",", "-", "-.", "-,"}:
        return 0
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return int(round(float(cleaned)))
    except ValueError:
        return 0


def _parse_emm_csv(text: str) -> list[Player]:
    text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []

    headers = list(reader.fieldnames)
    if any(len(name) > 80 for name in headers):
        logger.error("Sheet response is not a valid CSV header row")
        return []

    field_map = _map_fields(
        headers,
        {
            "object_number": ("objectnumber", "object number"),
            "address": ("адрес", "address"),
            "name": ("name", "имя", "название"),
            "emm": ("емм", "emm"),
            "reflash": ("перепрошить", "reflash"),
            "cube": ("обновить кубик", "кубик"),
            "do_nothing": ("ничего не делать",),
        },
    )
    required = ("object_number", "address", "name")
    if any(key not in field_map for key in required):
        logger.error("Unexpected EMM sheet headers: %s", headers)
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


def _parse_to_csv(text: str) -> list[ToVisit]:
    text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []

    headers = list(reader.fieldnames)
    if any(len(name) > 80 for name in headers):
        logger.error("TO sheet response is not a valid CSV header row")
        return []

    field_map = _map_fields(
        headers,
        {
            "name": ("название объекта", "name", "название"),
            "address": ("адрес", "address"),
            "work_type": ("вид работ",),
            "actual_cost": ("фактическая стоимость работ",),
            "extra_cost": ("доп затраты (компенсации)", "доп затраты"),
            "bitrix_task_id": ("задача bitrix", "bitrix"),
        },
    )
    if "name" not in field_map:
        logger.error("Unexpected TO sheet headers: %s", headers)
        return []

    visits: list[ToVisit] = []
    for row in reader:
        name = _cell(row, field_map["name"])
        if not name:
            continue
        visits.append(
            ToVisit(
                name=name,
                address=_cell(row, field_map.get("address")),
                work_type=_cell(row, field_map.get("work_type")),
                actual_cost=parse_money(_cell(row, field_map.get("actual_cost"))),
                extra_cost=parse_money(_cell(row, field_map.get("extra_cost"))),
                bitrix_task_id=_cell(row, field_map.get("bitrix_task_id")),
            )
        )
    return visits


def _parse_directory_csv(text: str) -> list[str]:
    text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []

    headers = [h for h in reader.fieldnames if h]
    field_map = _map_fields(
        headers,
        {
            "project": ("проекты", "проект", "projects", "project"),
        },
    )
    key = field_map.get("project") or (headers[0] if headers else None)
    if not key:
        return []

    names: list[str] = []
    seen: set[str] = set()
    for row in reader:
        name = _cell(row, key)
        if not name:
            continue
        marker = name.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        names.append(name)
    return names


def _parse_project_csv(text: str, project: str) -> list[ProjectStore]:
    text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []

    headers = [h for h in reader.fieldnames if h]
    if any(len(name) > 120 for name in headers):
        logger.error("Project sheet %s response is not a valid CSV header row", project)
        return []

    field_map = _map_fields(
        headers,
        {
            "name": (
                "название тт",
                "название объекта",
                "название",
                "уникальный 6-код",
                "код тт",
                "тк",
                "номер дм",
            ),
            "region": ("регион", "область", "region"),
            "address": ("адрес", "address"),
            "manager": ("менеджер", "manager"),
            "code": (
                "уникальный 6-код",
                "код тт",
                "тк",
                "номер дм",
                "objectnumber",
                "object number",
            ),
        },
    )
    if "name" not in field_map:
        logger.error("Unexpected project sheet %s headers: %s", project, headers[:20])
        return []

    stores: list[ProjectStore] = []
    for row in reader:
        name = _cell(row, field_map["name"])
        if not name:
            continue
        codes: list[str] = []
        code = _cell(row, field_map.get("code"))
        if code and code.casefold() != name.casefold():
            codes.append(code)
        stores.append(
            ProjectStore(
                project=project,
                name=name,
                region=_cell(row, field_map.get("region")),
                address=_cell(row, field_map.get("address")),
                manager=_cell(row, field_map.get("manager")),
                codes=tuple(codes),
            )
            )
        return stores


def _parse_do_csv(text: str, project: str) -> list[DoStore]:
    text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []

    headers = [h for h in reader.fieldnames if h]
    if any(len(name) > 120 for name in headers):
        logger.error("DO sheet %s response is not a valid CSV header row", project)
        return []

    field_map = _map_fields(
        headers,
        {
            "name": ("название тт", "название объекта", "название"),
            "region": ("регион", "область", "region"),
            "address": ("адрес", "address"),
            "manager": ("менеджер", "manager"),
            "logistics": ("логистика",),
            "expense_task": ("задача на расходку",),
            "order_date": ("дата заказа",),
            "acceptance": ("принятие объекта",),
        },
    )
    if "name" not in field_map:
        logger.error("Unexpected DO sheet %s headers: %s", project, headers[:20])
        return []

    stores: list[DoStore] = []
    for row in reader:
        name = _cell(row, field_map["name"])
        if not name:
            continue
        stores.append(
            DoStore(
                project=project,
                name=name,
                region=_cell(row, field_map.get("region")),
                address=_cell(row, field_map.get("address")),
                manager=_cell(row, field_map.get("manager")),
                logistics=_cell(row, field_map.get("logistics")),
                expense_task=_cell(row, field_map.get("expense_task")),
                order_date_raw=_cell(row, field_map.get("order_date")),
                acceptance=_cell(row, field_map.get("acceptance")),
            )
        )
    return stores


def _map_fields(fieldnames: list[str], aliases: dict[str, tuple[str, ...]]) -> dict[str, str]:
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
