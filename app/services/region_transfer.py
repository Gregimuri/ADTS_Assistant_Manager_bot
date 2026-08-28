from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO

from openpyxl import Workbook

from app.services.sheets import ProjectRow, SheetsClient, SheetsError

_COMPLETED_STATUS = "выполнен"
_TAG_RE = re.compile(r"#передатьрегионы", re.IGNORECASE)
_LIST_SPLIT_RE = re.compile(r"[,;]")


@dataclass(frozen=True, slots=True)
class RegionTransferRequest:
    projects: tuple[str, ...]
    manager: str
    regions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RegionTransferResult:
    sheets: dict[str, list[ProjectRow]]
    total_rows: int


def parse_region_transfer_message(text: str) -> RegionTransferRequest | None:
    if not _TAG_RE.search(text):
        return None

    projects: list[str] = []
    manager = ""
    regions: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lower = line.casefold()
        if lower.startswith("проекты:"):
            projects = _split_values(line.split(":", 1)[1])
        elif lower.startswith("менеджер:"):
            manager = line.split(":", 1)[1].strip()
        elif lower.startswith("регионы:"):
            regions = _split_values(line.split(":", 1)[1])

    if not projects or not manager or not regions:
        return None
    return RegionTransferRequest(
        projects=tuple(projects),
        manager=manager,
        regions=tuple(regions),
    )


def _split_values(raw: str) -> list[str]:
    values: list[str] = []
    for part in _LIST_SPLIT_RE.split(raw):
        item = part.strip()
        if item:
            values.append(item)
    return values


class RegionTransferService:
    def __init__(self, sheets: SheetsClient) -> None:
        self._sheets = sheets

    async def list_transfer_projects(self) -> list[str]:
        names = await self._sheets.get_project_names()
        return [name for name in names if name.casefold() != "то"]

    async def resolve_projects(self, raw_projects: list[str]) -> list[str]:
        available = await self.list_transfer_projects()
        project_map = {name.casefold(): name for name in available}
        resolved: list[str] = []
        for raw in raw_projects:
            key = raw.strip().casefold()
            if not key:
                continue
            match = project_map.get(key)
            if not match:
                raise ValueError(f"Проект «{raw.strip()}» не найден в справочнике.")
            if match not in resolved:
                resolved.append(match)
        if not resolved:
            raise ValueError("Не выбран ни один проект.")
        return resolved

    async def list_managers(self, projects: list[str]) -> list[str]:
        managers: set[str] = set()
        for project in projects:
            for row in await self._sheets.get_project_rows(project):
                manager = row.manager.strip()
                if manager:
                    managers.add(manager)
        return sorted(managers, key=str.casefold)

    async def list_regions(self, projects: list[str], manager: str) -> list[str]:
        regions: set[str] = set()
        for project in projects:
            for row in await self._sheets.get_project_rows(project):
                if not _manager_matches(row.manager, manager):
                    continue
                region = row.region.strip()
                if region:
                    regions.add(region)
        return sorted(regions, key=str.casefold)

    async def build_result(self, request: RegionTransferRequest) -> RegionTransferResult:
        projects = await self.resolve_projects(list(request.projects))
        sheets: dict[str, list[ProjectRow]] = {}
        total_rows = 0
        for project in projects:
            rows = await self._filter_project_rows(
                project,
                manager=request.manager,
                regions=request.regions,
            )
            if rows:
                sheets[project] = rows
                total_rows += len(rows)
        return RegionTransferResult(sheets=sheets, total_rows=total_rows)

    async def _filter_project_rows(
        self,
        project: str,
        *,
        manager: str,
        regions: list[str] | tuple[str, ...],
    ) -> list[ProjectRow]:
        rows: list[ProjectRow] = []
        for row in await self._sheets.get_project_rows(project):
            if not _manager_matches(row.manager, manager):
                continue
            if not any(_region_matches(row.region, region) for region in regions):
                continue
            if _normalize(row.smr_status) == _COMPLETED_STATUS:
                continue
            rows.append(row)
        return rows


def build_region_transfer_excel(result: RegionTransferResult) -> BytesIO:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for project, rows in result.sheets.items():
        sheet = workbook.create_sheet(title=_safe_sheet_title(project))
        sheet.append(["Название", "Регион", "Адрес", "Статус СМР"])
        for row in rows:
            sheet.append([row.name, row.region, row.address, row.smr_status])
    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer


def _safe_sheet_title(project: str) -> str:
    title = re.sub(r"[\[\]:*?/\\]", " ", project).strip() or "Проект"
    return title[:31]


def _normalize(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _normalize_region(value: str) -> str:
    return _normalize(value).replace(".", "")


def _manager_matches(actual: str, expected: str) -> bool:
    return _normalize(actual) == _normalize(expected)


def _region_matches(actual: str, expected: str) -> bool:
    left = _normalize_region(actual)
    right = _normalize_region(expected)
    if not left or not right:
        return False
    return left == right or left.startswith(right) or right.startswith(left)
