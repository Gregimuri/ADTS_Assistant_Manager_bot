from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from enum import Enum

from aiogram import Bot

from app.config import Settings
from app.services.assembly_reports import AssemblyReportsService, BitrixTasksError
from app.services.exit_reports import ExitReportsService
from app.services.report_delivery import send_report_text
from app.services.sheets import SheetsError

logger = logging.getLogger(__name__)

_MSK = timezone(timedelta(hours=3))
_MORNING_TIME = time(9, 0)
_EVENING_TIME = time(17, 30)


class _ScheduleSlot(str, Enum):
    MORNING = "morning"
    EVENING = "evening"


async def run_admin_report_scheduler(
    bot: Bot,
    exit_reports: ExitReportsService,
    assembly_reports: AssemblyReportsService,
    settings: Settings,
) -> None:
    """По будням в 9:00 и 17:30 МСК отправляет админ-отчёты в группу."""
    while True:
        now = datetime.now(_MSK)
        next_run, slot = _next_weekday_run(now)
        delay = (next_run - now).total_seconds()
        logger.info(
            "Next admin report (%s) scheduled at %s (in %.0f s)",
            slot.value,
            next_run.isoformat(),
            delay,
        )
        await asyncio.sleep(delay)
        try:
            if slot is _ScheduleSlot.MORNING:
                await _send_morning_reports(bot, exit_reports, assembly_reports, settings)
            else:
                await _send_evening_reports(bot, exit_reports, assembly_reports, settings)
        except Exception:
            logger.exception("Scheduled admin report failed (%s)", slot.value)


async def _send_morning_reports(
    bot: Bot,
    exit_reports: ExitReportsService,
    assembly_reports: AssemblyReportsService,
    settings: Settings,
) -> None:
    projects = await _scheduled_projects(exit_reports, settings)
    try:
        text = await exit_reports.build_exit_plan(projects)
        await send_report_text(bot, settings, text)
        logger.info("Scheduled exit plan sent")
    except SheetsError:
        logger.exception("Scheduled exit plan: failed to load sheet")
    except Exception:
        logger.exception("Scheduled exit plan failed")

    try:
        text = await assembly_reports.build_assembly_snapshot()
        await send_report_text(bot, settings, text)
        logger.info("Scheduled assembly snapshot sent")
    except BitrixTasksError:
        logger.exception("Scheduled assembly snapshot: Bitrix error")
    except Exception:
        logger.exception("Scheduled assembly snapshot failed")


async def _send_evening_reports(
    bot: Bot,
    exit_reports: ExitReportsService,
    assembly_reports: AssemblyReportsService,
    settings: Settings,
) -> None:
    projects = await _scheduled_projects(exit_reports, settings)
    try:
        text = await exit_reports.build_exit_report(projects)
        await send_report_text(bot, settings, text)
        logger.info("Scheduled exit report sent")
    except SheetsError:
        logger.exception("Scheduled exit report: failed to load sheet")
    except Exception:
        logger.exception("Scheduled exit report failed")

    try:
        text = await assembly_reports.build_assembly_report()
        await send_report_text(bot, settings, text)
        logger.info("Scheduled assembly report sent")
    except BitrixTasksError:
        logger.exception("Scheduled assembly report: Bitrix error")
    except Exception:
        logger.exception("Scheduled assembly report failed")


async def _scheduled_projects(
    exit_reports: ExitReportsService,
    settings: Settings,
) -> list[str]:
    raw = [part.strip() for part in settings.scheduled_exit_projects.split(",") if part.strip()]
    return await exit_reports.resolve_projects(raw)


def _next_weekday_run(now: datetime) -> tuple[datetime, _ScheduleSlot]:
    schedule = (
        (_MORNING_TIME, _ScheduleSlot.MORNING),
        (_EVENING_TIME, _ScheduleSlot.EVENING),
    )
    candidates: list[tuple[datetime, _ScheduleSlot]] = []
    for days_ahead in range(8):
        day = (now + timedelta(days=days_ahead)).date()
        if day.weekday() >= 5:
            continue
        for schedule_time, slot in schedule:
            run_at = datetime.combine(day, schedule_time, tzinfo=_MSK)
            if run_at > now:
                candidates.append((run_at, slot))
    if candidates:
        return min(candidates, key=lambda item: item[0])
    fallback_day = now.date() + timedelta(days=1)
    while fallback_day.weekday() >= 5:
        fallback_day += timedelta(days=1)
    return datetime.combine(fallback_day, _MORNING_TIME, tzinfo=_MSK), _ScheduleSlot.MORNING
