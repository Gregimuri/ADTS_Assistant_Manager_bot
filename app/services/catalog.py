from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass

from app.services.sheets import Player, SheetsClient


@dataclass(frozen=True, slots=True)
class StoreMatch:
    query: str
    object_number: str
    address: str
    players: tuple[Player, ...]
    emm_count: int
    flash_count: int
    cube_count: int


class Catalog:
    def __init__(self, sheets: SheetsClient) -> None:
        self._sheets = sheets

    async def find_stores(self, query: str) -> list[StoreMatch]:
        players = await self._sheets.get_players()
        matched = [player for player in players if _name_matches(player.name, query)]
        if not matched:
            return []

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
                    emm_count=sum(1 for player in group if _is_emm_device(player.emm)),
                    flash_count=sum(1 for player in group if _is_reflash(player.reflash)),
                    cube_count=sum(1 for player in group if _is_on_site_cube(player.cube)),
                )
            )
        return result


def _name_matches(player_name: str, query: str) -> bool:
    pattern = rf"(?<!\w){re.escape(query)}(?!\w)"
    return bool(re.search(pattern, player_name, flags=re.IGNORECASE))


def _normalize(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _is_emm_device(status: str) -> bool:
    normalized = _normalize(status)
    return bool(normalized) and normalized != "не ставить"


def _is_reflash(status: str) -> bool:
    return _normalize(status) == "прошить"


def _is_on_site_cube(status: str) -> bool:
    return _normalize(status) == "обновить"
