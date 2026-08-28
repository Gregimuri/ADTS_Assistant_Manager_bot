from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone

from aiogram import Bot

from app.config import Settings
from app.services.catalog import Catalog
from app.services.invoice import build_do_report_blocks
from app.services.sheets import SheetsError

logger = logging.getLogger(__name__)

_MSK = timezone(timedelta(hours=3))
_SCHEDULE_TIME = time(9, 5)


async def send_do_report(bot: Bot, catalog: Catalog, settings: Settings) -> int:
    """Формирует и отправляет отчёт #ДО в целевую группу. Возвращает число ТТ."""
    matches = await catalog.find_do_report_stores(settings)
    chunks = build_do_report_blocks(matches)
    if not chunks:
        logger.info("DO report is empty, nothing to send")
        return 0

    chat_id = settings.do_report_chat_id
    for chunk in chunks:
        await bot.send_message(chat_id, chunk)
    logger.info("DO report sent to chat_id=%s (%s TT)", chat_id, len(matches))
    return len(matches)


async def run_do_report_scheduler(
    bot: Bot,
    catalog: Catalog,
    settings: Settings,
) -> None:
    """Каждый будний день в 9:05 МСК отправляет отчёт #ДО."""
    while True:
        now = datetime.now(_MSK)
        next_run = _next_weekday_run(now)
        delay = (next_run - now).total_seconds()
        logger.info("Next DO report scheduled at %s (in %.0f s)", next_run.isoformat(), delay)
        await asyncio.sleep(delay)
        try:
            await send_do_report(bot, catalog, settings)
        except SheetsError:
            logger.exception("Scheduled DO report: failed to load sheet")
        except Exception:
            logger.exception("Scheduled DO report failed")


def _next_weekday_run(now: datetime) -> datetime:
    for days_ahead in range(8):
        day = (now + timedelta(days=days_ahead)).date()
        if day.weekday() >= 5:
            continue
        run_at = datetime.combine(day, _SCHEDULE_TIME, tzinfo=_MSK)
        if run_at > now:
            return run_at
    fallback = now + timedelta(days=1)
    return fallback.replace(hour=9, minute=5, second=0, microsecond=0, tzinfo=_MSK)
