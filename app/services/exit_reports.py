from __future__ import annotations

import asyncio
import re
from datetime import date

from app.services.dates import format_ru_date, msk_today, msk_yesterday, parse_ru_date
from app.services.report_storage import ReportStorage
from app.services.sheets import ProjectExitRow, SheetsClient

_TAG_EXIT_PLAN = re.compile(r"#планвыходов", re.IGNORECASE)
_TAG_EXIT_REPORT = re.compile(r"#отчетвыходов", re.IGNORECASE)
_TAG_ASSEMBLY = re.compile(r"#сборкарасходников", re.IGNORECASE)
_TAG_ASSEMBLY_REPORT = re.compile(r"#отчетсборки", re.IGNORECASE)

_STATUS_COMPLETED = "выполнен"
_STATUS_FINAL = "финальный"
_DO_CUMULATIVE_UNTIL = date(2026, 9, 11)


class ExitReportsService:
    def __init__(self, sheets: SheetsClient, storage: ReportStorage) -> None:
        self._sheets = sheets
        self._storage = storage

    async def list_projects(self) -> list[str]:
        return await self._sheets.get_project_names()

    async def resolve_projects(self, raw_projects: list[str]) -> list[str]:
        available = await self.list_projects()
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

    async def build_exit_plan(self, display_projects: list[str], *, day: date | None = None) -> str:
        today = day or msk_today()
        resolved = await self.resolve_projects(display_projects)
        all_projects = await self.list_projects()
        counts = await self._count_exits_for_projects(all_projects, today)
        await self._storage.save_exit_plan(today, counts)

        lines = [f"План на день {format_ru_date(today)}:", ""]
        for project in resolved:
            lines.append(f"{project} - {counts.get(project, 0)}")
        return "\n".join(lines)

    async def build_exit_report(
        self,
        display_projects: list[str],
        *,
        day: date | None = None,
    ) -> str:
        report_day = day or msk_yesterday()
        resolved = await self.resolve_projects(display_projects)
        lines = [f"ОТЧЕТ {format_ru_date(report_day)}", ""]

        for project in resolved:
            plan = self._storage.get_exit_plan(report_day, project)
            if plan is None:
                # JSON мог потеряться после рестарта — пересчитываем план за тот день
                plan = await self._count_exits_for_project(project, report_day)
            rows = await self._rows_with_exit_on(project, report_day)
            completed = sum(1 for row in rows if _is_completed_status(row.smr_status))
            final = sum(1 for row in rows if _is_final_status(row.smr_status))
            # Факт = все выходы за день; «из них» — разбивка по статусам СМР
            fact = len(rows)
            lines.extend(
                [
                    project,
                    f"- Утренний план был: {plan}",
                    f"- Факт: {fact}",
                    "- Из них:",
                    f"    - Выполнен: {completed}",
                    f"    - Финальный: {final}",
                    "",
                ]
            )
        return "\n".join(lines).rstrip()

    async def build_do_cumulative_report(
        self,
        *,
        until: date | None = None,
        report_day: date | None = None,
        project: str = "ДО",
    ) -> str:
        deadline = until or _DO_CUMULATIVE_UNTIL
        today = report_day or msk_today()
        resolved = (await self.resolve_projects([project]))[0]
        rows = await self._sheets.get_project_exit_rows(resolved)

        seen: set[str] = set()
        total = 0
        done = 0
        for row in rows:
            if row.name in seen:
                continue
            mount_date = _row_mount_date(row)
            if mount_date is None or mount_date > deadline:
                continue
            seen.add(row.name)
            total += 1
            if _is_completed_status(row.smr_status) or _is_final_status(row.smr_status):
                done += 1

        remaining = total - done
        deadline_short = deadline.strftime("%d.%m")
        return "\n".join(
            [
                f"ОТЧЕТ по ДО до {deadline_short} на {format_ru_date(today)}",
                f"Всего - {total} ТТ",
                f"Сделано (финальный/выполнен) - {done} ТТ",
                f"Осталось - {remaining} ТТ",
            ]
        )

    async def _count_exits_for_projects(
        self,
        projects: list[str],
        target_day: date,
    ) -> dict[str, int]:
        results = await asyncio.gather(
            *[self._count_exits_for_project(project, target_day) for project in projects],
            return_exceptions=True,
        )
        counts: dict[str, int] = {}
        for project, result in zip(projects, results, strict=True):
            if isinstance(result, Exception):
                counts[project] = 0
                continue
            counts[project] = result
        return counts

    async def _count_exits_for_project(self, project: str, target_day: date) -> int:
        rows = await self._rows_with_exit_on(project, target_day)
        return len(rows)

    async def _rows_with_exit_on(self, project: str, target_day: date) -> list[ProjectExitRow]:
        rows = await self._sheets.get_project_exit_rows(project)
        matched: list[ProjectExitRow] = []
        seen: set[str] = set()
        for row in rows:
            if row.name in seen:
                continue
            if not _row_has_exit_on(row, target_day):
                continue
            seen.add(row.name)
            matched.append(row)
        return matched


def parse_project_list_message(text: str, tag_re: re.Pattern[str]) -> list[str] | None:
    if not tag_re.search(text):
        return None
    projects: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.casefold().startswith("проекты:"):
            projects.extend(_split_project_values(line.split(":", 1)[1]))
            continue
        projects.append(line)
    if not projects:
        return None
    return projects


def parse_exit_plan_message(text: str) -> list[str] | None:
    return parse_project_list_message(text, _TAG_EXIT_PLAN)


def parse_exit_report_message(text: str) -> list[str] | None:
    return parse_project_list_message(text, _TAG_EXIT_REPORT)


def has_assembly_tag(text: str) -> bool:
    return bool(_TAG_ASSEMBLY.search(text))


def has_assembly_report_tag(text: str) -> bool:
    return bool(_TAG_ASSEMBLY_REPORT.search(text))


def _split_project_values(raw: str) -> list[str]:
    values: list[str] = []
    for part in re.split(r"[,;]", raw):
        item = part.strip()
        if item:
            values.append(item)
    return values


def _row_has_exit_on(row: ProjectExitRow, target_day: date) -> bool:
    for raw in row.exit_date_values:
        parsed = parse_ru_date(raw)
        if parsed == target_day:
            return True
    return False


def _row_mount_date(row: ProjectExitRow) -> date | None:
    if not row.exit_date_values:
        return None
    return parse_ru_date(row.exit_date_values[0])


def _normalize_status(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _is_completed_status(value: str) -> bool:
    status = _normalize_status(value)
    return status == _STATUS_COMPLETED or status.startswith(f"{_STATUS_COMPLETED} ")


def _is_final_status(value: str) -> bool:
    status = _normalize_status(value)
    return status == _STATUS_FINAL or status.startswith(f"{_STATUS_FINAL} ")
