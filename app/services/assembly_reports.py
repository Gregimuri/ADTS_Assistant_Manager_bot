from __future__ import annotations

from datetime import date

from app.config import Settings
from app.services.bitrix_tasks import (
    BitrixTasksClient,
    BitrixTasksError,
    count_assembly_completed_today,
    count_assembly_created_today,
    count_open_assembly_before_today,
    count_open_assembly_tasks,
)
from app.services.dates import format_ru_date, msk_today


class AssemblyReportsService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bitrix = BitrixTasksClient(settings)

    async def build_assembly_snapshot(self, *, day: date | None = None) -> str:
        today = day or msk_today()
        tasks = await self._bitrix.list_assembly_tasks()
        count = count_open_assembly_tasks(tasks)
        return (
            f"{format_ru_date(today)}\n\n"
            f"Сборка расходников кол-во задач - {count}"
        )

    async def build_assembly_report(self, *, day: date | None = None) -> str:
        today = day or msk_today()
        tasks = await self._bitrix.list_assembly_tasks()
        morning_count = count_open_assembly_before_today(tasks, today)
        new_today = count_assembly_created_today(tasks, today)
        completed_today = count_assembly_completed_today(tasks, today)
        return (
            f"ОТЧЕТ {format_ru_date(today)}\n\n"
            f"Утром сборка расходников кол-во задач - {morning_count}\n"
            f"В течении дня кол-во новых задач на сборку - {new_today}\n"
            f"Завершено задач сборка расходников - {completed_today}"
        )


__all__ = ["AssemblyReportsService", "BitrixTasksError"]
