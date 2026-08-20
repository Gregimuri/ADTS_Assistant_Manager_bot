from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass

from app.services.sheets import Player, SheetsClient, ToVisit


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
