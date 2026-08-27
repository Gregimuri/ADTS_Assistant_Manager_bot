from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.config import Settings
from app.services.sheets import DoStore, Player, ProjectStore, SheetsClient, ToVisit

_MSK = timezone(timedelta(hours=3))
_DATE_FORMATS = ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%y")


@dataclass(frozen=True, slots=True)
class StoreMatch:
    query: str
    object_number: str
    address: str
    players: tuple[Player, ...]
    emm_count: int
    flash_count: int
    cube_count: int
    bitrix_task_id: str = ""


@dataclass(frozen=True, slots=True)
class ToMatch:
    query: str
    visit: ToVisit


@dataclass(frozen=True, slots=True)
class InfoMatch:
    query: str
    project: str
    name: str
    region: str
    address: str
    manager: str


@dataclass(frozen=True, slots=True)
class InfoQuery:
    raw: str
    search: str
    project: str | None = None


class Catalog:
    def __init__(self, sheets: SheetsClient) -> None:
        self._sheets = sheets

    async def find_stores(self, query: str) -> list[StoreMatch]:
        players = await self._sheets.get_players()
        matched = [player for player in players if _name_matches(player.name, query)]
        if not matched:
            return []

        bitrix_task_id = await self.find_emm_bitrix_task(query)

        groups: OrderedDict[str, list[Player]] = OrderedDict()
        for player in matched:
            groups.setdefault(player.object_number, []).append(player)

        result: list[StoreMatch] = []
        for object_number, group in groups.items():
            result.append(
                StoreMatch(
                    query=query,
                    object_number=object_number,
                    address=group[0].address,
                    players=tuple(group),
                    emm_count=sum(1 for player in group if _is_emm_device(player)),
                    flash_count=sum(1 for player in group if _is_reflash(player)),
                    cube_count=sum(1 for player in group if _is_on_site_cube(player)),
                    bitrix_task_id=bitrix_task_id,
                )
            )
        return result

    async def find_to_visits(self, query: str) -> list[ToMatch]:
        visits = await self._sheets.get_to_visits()
        return [
            ToMatch(query=query, visit=visit)
            for visit in visits
            if _name_matches(visit.name, query)
        ]

    async def find_emm_bitrix_task(self, query: str) -> str:
        """Ищет в листе ТО задачу Bitrix, если в виде работ есть «ЕММ»."""
        visits = await self._sheets.get_to_visits()
        task_id = ""
        for visit in visits:
            if not _name_matches(visit.name, query):
                continue
            if "емм" not in visit.work_type.casefold():
                continue
            if visit.bitrix_task_id.strip():
                task_id = visit.bitrix_task_id.strip()
        return task_id

    async def parse_info_queries(self, lines: list[str]) -> list[InfoQuery]:
        projects = await self._sheets.get_project_names()
        project_map = {name.casefold(): name for name in projects}
        queries: list[InfoQuery] = []
        for raw in lines:
            parts = raw.split(maxsplit=1)
            if len(parts) == 2 and parts[0].casefold() in project_map:
                queries.append(
                    InfoQuery(
                        raw=raw,
                        project=project_map[parts[0].casefold()],
                        search=parts[1].strip(),
                    )
                )
            else:
                queries.append(InfoQuery(raw=raw, search=raw))
        return queries

    async def find_info_stores(self, query: InfoQuery) -> list[InfoMatch]:
        if query.project:
            stores = await self._sheets.get_project_stores(query.project)
        else:
            stores = await self._sheets.get_all_project_stores()

        matches: list[InfoMatch] = []
        seen: set[tuple[str, str, str, str, str]] = set()
        for store in stores:
            if not _project_store_matches(store, query.search):
                continue
            key = (store.project, store.name, store.region, store.address, store.manager)
            if key in seen:
                continue
            seen.add(key)
            matches.append(
                InfoMatch(
                    query=query.raw,
                    project=store.project,
                    name=store.name,
                    region=store.region,
                    address=store.address,
                    manager=store.manager,
                )
            )
        return matches

    async def find_do_report_stores(self, settings: Settings) -> list[InfoMatch]:
        stores = await self._sheets.get_do_stores()
        today = datetime.now(_MSK).date()
        deadline = today + timedelta(days=settings.do_order_horizon_days)
        matches: list[InfoMatch] = []
        seen: set[tuple[str, str, str, str]] = set()
        for store in stores:
            if not _do_store_matches(store, deadline):
                continue
            key = (store.name, store.region, store.address, store.manager)
            if key in seen:
                continue
            seen.add(key)
            matches.append(
                InfoMatch(
                    query=store.name,
                    project=store.project,
                    name=store.name,
                    region=store.region,
                    address=store.address,
                    manager=store.manager,
                )
            )
        return matches


def _do_store_matches(store: DoStore, deadline: date) -> bool:
    if store.logistics.strip():
        return False
    if store.expense_task.strip():
        return False
    if _normalize(store.acceptance) == "принят":
        return False
    order_date = _parse_ru_date(store.order_date_raw)
    if order_date is None:
        return False
    return order_date <= deadline


def _parse_ru_date(value: str) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _project_store_matches(store: ProjectStore, query: str) -> bool:
    if _name_matches(store.name, query):
        return True
    return any(_name_matches(code, query) for code in store.codes)


def _name_matches(player_name: str, query: str) -> bool:
    pattern = rf"(?<!\w){re.escape(query)}(?!\w)"
    return bool(re.search(pattern, player_name, flags=re.IGNORECASE))


def _normalize(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _is_skip_work(player: Player) -> bool:
    return _normalize(player.do_nothing) == "ничего не делать"


def _is_emm_device(player: Player) -> bool:
    if _is_skip_work(player):
        return False
    normalized = _normalize(player.emm)
    return bool(normalized) and normalized != "не ставить"


def _is_reflash(player: Player) -> bool:
    if _is_skip_work(player):
        return False
    return _normalize(player.reflash) == "прошить"


def _is_on_site_cube(player: Player) -> bool:
    if _is_skip_work(player):
        return False
    return _normalize(player.cube) == "обновить"
