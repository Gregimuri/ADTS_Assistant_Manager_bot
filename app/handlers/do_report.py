from __future__ import annotations

import logging
import re

from aiogram import Bot, F, Router
from aiogram.enums import MessageEntityType
from aiogram.filters import Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.config import Settings
from app.handlers.flows import answer_text, reply_do_report
from app.keyboards import BTN_BACK, BTN_DO_SEND, do_confirm_keyboard, main_keyboard
from app.services.catalog import Catalog
from app.services.do_report import send_do_report_chunks
from app.states import BotStates
from app.texts import (
    MSG_CANCELLED,
    MSG_DO_SEND_ERROR,
    MSG_DO_SENT,
    MSG_DO_SENT_COUNT,
    MSG_DO_USE_BUTTONS,
)

logger = logging.getLogger(__name__)

router = Router(name="do_report")

_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)
_DO_LINE_RE = re.compile(r"^#до(?:\s|$)", re.IGNORECASE)


def _normalize_tag_text(text: str) -> str:
    return text.translate(_INVISIBLE).replace("\xa0", " ").strip()


class DoTagFilter(Filter):
    """Срабатывает на сообщение, где первая строка — хэштег #ДО."""

    async def __call__(self, message: Message) -> bool:
        text = message.text or message.caption or ""
        if not text:
            return False

        normalized = _normalize_tag_text(text)
        if not normalized:
            return False

        first_line = normalized.splitlines()[0].strip()
        if first_line.casefold() == "#до":
            return True
        if _DO_LINE_RE.match(first_line) and not first_line[3:].strip():
            return True

        source = message.text or message.caption or ""
        for entity in message.entities or message.caption_entities or ():
            if entity.type != MessageEntityType.HASHTAG:
                continue
            frag = _normalize_tag_text(source[entity.offset : entity.offset + entity.length])
            if frag.casefold() == "#до":
                return True
        return False


@router.message(DoTagFilter())
async def handle_do_tag(
    message: Message,
    bot: Bot,
    catalog: Catalog,
    settings: Settings,
    state: FSMContext,
) -> None:
    logger.info(
        "Matched DO report tag from chat_id=%s user_id=%s",
        message.chat.id,
        message.from_user.id if message.from_user else None,
    )
    await state.clear()
    await reply_do_report(message, bot, catalog, settings, state)


@router.message(BotStates.waiting_do_confirm, F.text == BTN_DO_SEND)
async def confirm_do_send(
    message: Message,
    bot: Bot,
    settings: Settings,
    state: FSMContext,
) -> None:
    from aiogram.enums import ParseMode

    data = await state.get_data()
    chunks: list[str] = data.get("do_report_chunks", [])
    count: int = data.get("do_report_count", 0)
    await state.clear()
    try:
        await send_do_report_chunks(bot, settings, chunks)
    except Exception:
        logger.exception("Failed to send DO report after confirmation")
        await answer_text(
            message,
            MSG_DO_SEND_ERROR,
            reply_markup=main_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    text = MSG_DO_SENT if count == 0 else MSG_DO_SENT_COUNT.format(count=count)
    await answer_text(
        message,
        text,
        reply_markup=main_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@router.message(BotStates.waiting_do_confirm, F.text == BTN_BACK)
async def confirm_do_back(message: Message, state: FSMContext) -> None:
    from aiogram.enums import ParseMode

    await state.clear()
    await answer_text(
        message,
        MSG_CANCELLED,
        reply_markup=main_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@router.message(BotStates.waiting_do_confirm, F.text)
async def confirm_do_unknown(message: Message) -> None:
    from aiogram.enums import ParseMode

    await answer_text(
        message,
        MSG_DO_USE_BUTTONS,
        reply_markup=do_confirm_keyboard(),
        parse_mode=ParseMode.HTML,
    )
