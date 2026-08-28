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

    chat_ids = _report_chat_id_candidates(settings.do_report_chat_id)
    last_error: Exception | None = None
    for chat_id in chat_ids:
        try:
            for chunk in chunks:
                await bot.send_message(chat_id, chunk)
            logger.info("DO report sent to chat_id=%s (%s TT)", chat_id, len(matches))
            return len(matches)
        except Exception as exc:  # noqa: BLE001 — пробуем запасной формат chat_id
            last_error = exc
            logger.warning("Failed to send DO report to chat_id=%s: %s", chat_id, exc)

    raise RuntimeError(
        f"Could not send DO report to any of {chat_ids}: {last_error}"
    ) from last_error


def _report_chat_id_candidates(chat_id: int) -> list[int]:
    """Пробует указанный id и парный формат с/без префикса -100 для супергрупп."""
    candidates = [chat_id]
    absolute = abs(chat_id)
    as_text = str(absolute)
    if as_text.startswith("100") and len(as_text) > 3:
        candidates.append(-int(as_text[3:]))
    else:
        candidates.append(-int(f"100{absolute}"))
    unique: list[int] = []
    for value in candidates:
        if value not in unique:
            unique.append(value)
    return unique


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
