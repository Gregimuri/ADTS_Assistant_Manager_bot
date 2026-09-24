from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any

from aiogram import Bot

from app.config import Settings
from app.services.bitrix_rest import bitrix_call
from app.services.do_report import send_do_report_chunks

logger = logging.getLogger(__name__)

_MSK = timezone(timedelta(hours=3))
_SCHEDULE_TIME = time(9, 0)
_DEADLINE_HOUR = 10

SHB_PLAN_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "16wHsiEwpaVCRsgyihnwRXZd-3JJNP1BLOLKgXxTP7Rc/edit?gid=325221125#gid=325221125"
)

# Менеджеры ШБ → Bitrix user id
SHB_PLAN_MANAGER_IDS: tuple[tuple[str, int], ...] = (
    ("Большанина Людмила", 489),
    ("Гончарова Диана", 585),
    ("Заворотных Мария", 515),
    ("Ивашнева Анастасия", 355),
    ("Казак Светлана", 353),
    ("Майшева София", 403),
    ("Маркевич Екатерина", 593),
    ("Муха Екатерина", 351),
    ("Носонович Михаил", 343),
    ("Оруджев Никита", 487),
    ("Пузикова Ева", 591),
    ("Романов Владислав", 493),
)

SHB_PLAN_AUDITOR_ID = 211  # Галинский Георгий
SHB_PLAN_CREATOR_ID = 401  # Титков Григорий
SHB_PLAN_TASK_TITLE = "Выставление плана по ШБ"


@dataclass(frozen=True, slots=True)
class ShbPlanRunResult:
    notify_text: str
    created_task_ids: tuple[str, ...]
    failed: tuple[str, ...]


class ShbPlanTasksError(RuntimeError):
    """Не удалось создать задачи плана ШБ."""


class ShbPlanTasksService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def run(self, *, now: datetime | None = None) -> ShbPlanRunResult:
        moment = now or datetime.now(_MSK)
        day_phrase = _day_phrase(moment)
        deadline = _deadline_for_shb_plan(moment)
        description = _task_description(day_phrase)
        # Текст в группу — фиксированный; пятничную формулировку только в задаче.
        notify_text = (
            "Поставлена задача в Битрикс до 10-00 по выставлению плана "
            "на сегодняшний день по ШБ"
        )

        created: list[str] = []
        failed: list[str] = []
        for name, user_id in SHB_PLAN_MANAGER_IDS:
            try:
                task_id = await self._create_task(
                    responsible_id=user_id,
                    description=description,
                    deadline=deadline,
                )
                created.append(task_id)
                logger.info("SHB plan task %s for %s (%s)", task_id, name, user_id)
            except Exception as exc:  # noqa: BLE001 — собираем ошибки по менеджерам
                logger.exception("SHB plan task failed for %s (%s)", name, user_id)
                failed.append(f"{name}: {exc}")

        if not created and failed:
            raise ShbPlanTasksError(
                "Не удалось создать ни одной задачи плана ШБ:\n" + "\n".join(failed)
            )
        return ShbPlanRunResult(
            notify_text=notify_text,
            created_task_ids=tuple(created),
            failed=tuple(failed),
        )

    async def run_and_notify(self, bot: Bot, *, now: datetime | None = None) -> ShbPlanRunResult:
        result = await self.run(now=now)
        await send_do_report_chunks(bot, self._settings, [result.notify_text])
        return result

    async def _create_task(
        self,
        *,
        responsible_id: int,
        description: str,
        deadline: str,
    ) -> str:
        fields: dict[str, Any] = {
            "TITLE": SHB_PLAN_TASK_TITLE,
            "DESCRIPTION": description,
            "DESCRIPTION_IN_BBCODE": "N",
            "RESPONSIBLE_ID": responsible_id,
            "CREATED_BY": self._settings.bitrix_shb_plan_creator_id,
            "DEADLINE": deadline,
            "PRIORITY": 1,
            "AUDITORS": [self._settings.bitrix_shb_plan_auditor_id],
            # Контроль постановщиком + требование результата работы.
            "TASK_CONTROL": "Y",
            "SE_PARAMETER": [3],
        }
        result = await bitrix_call(
            self._settings,
            "tasks.task.add",
            {"fields": fields},
            json_body=True,
            timeout_seconds=60,
        )
        task = result.get("task") if isinstance(result, dict) else None
        if not isinstance(task, dict):
            raise ShbPlanTasksError("Bitrix не вернул задачу.")
        task_id = str(task.get("id") or task.get("ID") or "")
        if not task_id:
            raise ShbPlanTasksError("Bitrix не вернул ID задачи.")
        return task_id


def _day_phrase(now: datetime) -> str:
    # Пятница (weekday=4): план на сегодня и выходные.
    if now.weekday() == 4:
        return "на сегодняшний день и выходные"
    return "на сегодняшний день"


def _deadline_for_shb_plan(now: datetime) -> str:
    """До 10:00 МСК — сегодня 10:00; после 10:00 — завтра 10:00."""
    cutoff = now.replace(hour=_DEADLINE_HOUR, minute=0, second=0, microsecond=0)
    day = now.date() if now < cutoff else (now.date() + timedelta(days=1))
    return f"{day.isoformat()}T{_DEADLINE_HOUR:02d}:00:00+03:00"


def _task_description(day_phrase: str) -> str:
    return (
        f"В таблице ШБ ({SHB_PLAN_SHEET_URL}) на листе «Ежедневный отчет» "
        f"требуется проставить колонку «План» {day_phrase} — "
        "планируемое кол-во выходов ваших подрядчиков.\n\n"
        "К результату задачи нужно прикрепить подтверждение "
        "(скрин/файл с заполненным планом)."
    )


async def run_shb_plan_scheduler(bot: Bot, service: ShbPlanTasksService) -> None:
    """По будням в 9:00 МСК создаёт задачи плана ШБ и пишет в группу ДО."""
    while True:
        now = datetime.now(_MSK)
        next_run = _next_weekday_run(now)
        delay = (next_run - now).total_seconds()
        logger.info("Next SHB plan run at %s (in %.0f s)", next_run.isoformat(), delay)
        await asyncio.sleep(delay)
        try:
            result = await service.run_and_notify(bot)
            logger.info(
                "Scheduled SHB plan: created=%s failed=%s",
                len(result.created_task_ids),
                len(result.failed),
            )
            if result.failed:
                logger.warning("Scheduled SHB plan failures: %s", "; ".join(result.failed))
        except Exception:
            logger.exception("Scheduled SHB plan failed")


def _next_weekday_run(now: datetime) -> datetime:
    for days_ahead in range(8):
        day = (now + timedelta(days=days_ahead)).date()
        if day.weekday() >= 5:
            continue
        run_at = datetime.combine(day, _SCHEDULE_TIME, tzinfo=_MSK)
        if run_at > now:
            return run_at
    fallback = now.date() + timedelta(days=1)
    while fallback.weekday() >= 5:
        fallback += timedelta(days=1)
    return datetime.combine(fallback, _SCHEDULE_TIME, tzinfo=_MSK)
