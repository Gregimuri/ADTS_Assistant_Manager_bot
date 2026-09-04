from __future__ import annotations

from datetime import date

from app.config import Settings
from app.services.bitrix_tasks import (
    BitrixTasksClient,
    BitrixTasksError,
    BitrixTask,
    count_assembly_completed_today,
    count_assembly_created_today,
    count_open_assembly_tasks,
)
from app.services.dates import format_ru_date, msk_today
from app.services.report_storage import ReportStorage


class AssemblyReportsService:
    def __init__(self, settings: Settings, storage: ReportStorage) -> None:
        self._settings = settings
        self._storage = storage
        self._bitrix = BitrixTasksClient(settings)

    async def build_assembly_snapshot(self, *, day: date | None = None) -> str:
        """Фиксирует все незавершённые задачи сборки на текущий момент."""
        today = day or msk_today()
        tasks = await self._bitrix.list_assembly_tasks()
        open_tasks = [task for task in tasks if _is_open_task(task)]
        open_ids = [task.task_id for task in open_tasks]
        count = len(open_ids)
        await self._storage.save_assembly_snapshot(
            today,
            open_count=count,
            task_ids=open_ids,
        )
        return (
            f"{format_ru_date(today)}\n\n"
            f"Сборка расходников кол-во задач - {count}"
        )

    async def build_assembly_report(self, *, day: date | None = None) -> str:
        today = day or msk_today()
        tasks = await self._bitrix.list_assembly_tasks()
        new_today = count_assembly_created_today(tasks, today)
        completed_today = count_assembly_completed_today(tasks, today)
        remaining = count_open_assembly_tasks(tasks)
        return (
            f"В течении дня кол-во новых задач на сборку - {new_today}\n"
            f"В течении дня завершено задач на сборку - {completed_today}\n"
            f"Осталось в работе задач на сборку - {remaining}"
        )


def _is_open_task(task: BitrixTask) -> bool:
    status = task.real_status or task.status
    return status != 5


__all__ = ["AssemblyReportsService", "BitrixTasksError"]
