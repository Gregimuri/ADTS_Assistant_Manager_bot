from __future__ import annotations

import logging

from aiogram import Bot

from app.config import Settings

logger = logging.getLogger(__name__)


def report_chat_id_candidates(chat_id: int) -> list[int]:
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


async def send_report_text(bot: Bot, settings: Settings, text: str) -> None:
    chat_id = settings.admin_report_chat_id
    chat_ids = report_chat_id_candidates(chat_id)
    last_error: Exception | None = None
    for target_chat_id in chat_ids:
        try:
            await bot.send_message(target_chat_id, text)
            logger.info("Admin report sent to chat_id=%s", target_chat_id)
            return
        except Exception as exc:  # noqa: BLE001 — пробуем запасной формат chat_id
            last_error = exc
            logger.warning("Failed to send admin report to chat_id=%s: %s", target_chat_id, exc)
    raise RuntimeError(
        f"Could not send admin report to any of {chat_ids}: {last_error}"
    ) from last_error
