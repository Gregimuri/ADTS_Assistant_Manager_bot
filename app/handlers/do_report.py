from __future__ import annotations

import logging
import re

from aiogram import Bot, F, Router
from aiogram.enums import MessageEntityType, ParseMode
from aiogram.filters import Filter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import Message

from app.access import can_use_do_report, keyboard_for_message
from app.chat_utils import is_group_chat
from app.config import Settings
from app.handlers.flows import answer_text, reply_do_report
from app.keyboards import BTN_BACK, BTN_DO, BTN_DO_SEND, do_confirm_keyboard
from app.services.catalog import Catalog
from app.services.do_report import send_do_report_chunks
from app.states import BotStates
from app.texts import (
    MSG_CANCELLED,
    MSG_DO_ACCESS_DENIED,
    MSG_DO_GROUP_HINT,
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


def _user_id(message: Message) -> int | None:
    return message.from_user.id if message.from_user else None


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


async def _deny_do_access(message: Message, settings: Settings) -> None:
    if is_group_chat(message):
        return
    await answer_text(
        message,
        MSG_DO_ACCESS_DENIED,
        reply_markup=keyboard_for_message(settings, message),
        parse_mode=ParseMode.HTML,
    )


async def _start_do_report(
    message: Message,
    bot: Bot,
    catalog: Catalog,
    settings: Settings,
    state: FSMContext,
) -> None:
    user_id = _user_id(message)
    if not can_use_do_report(settings, user_id):
        await _deny_do_access(message, settings)
        return
    if is_group_chat(message):
        await state.clear()
        await answer_text(message, MSG_DO_GROUP_HINT, parse_mode=ParseMode.HTML)
        return
    await state.clear()
    await reply_do_report(message, bot, catalog, settings, state)


@router.message(F.text == BTN_DO, StateFilter(default_state))
async def start_do_report_button(
    message: Message,
    bot: Bot,
    catalog: Catalog,
    settings: Settings,
    state: FSMContext,
) -> None:
    await _start_do_report(message, bot, catalog, settings, state)


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
        _user_id(message),
    )
    await _start_do_report(message, bot, catalog, settings, state)


@router.message(BotStates.waiting_do_confirm, F.text == BTN_DO_SEND)
async def confirm_do_send(
    message: Message,
    bot: Bot,
    settings: Settings,
    state: FSMContext,
) -> None:
    user_id = _user_id(message)
    data = await state.get_data()
    if not can_use_do_report(settings, user_id):
        await state.clear()
        await _deny_do_access(message, settings)
        return
    if data.get("do_report_user_id") != user_id:
        await state.clear()
        await answer_text(
            message,
            MSG_CANCELLED,
            reply_markup=keyboard_for_message(settings, message),
            parse_mode=ParseMode.HTML,
        )
        return

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
            reply_markup=keyboard_for_message(settings, message),
            parse_mode=ParseMode.HTML,
        )
        return

    text = MSG_DO_SENT if count == 0 else MSG_DO_SENT_COUNT.format(count=count)
    await answer_text(
        message,
        text,
        reply_markup=keyboard_for_message(settings, message),
        parse_mode=ParseMode.HTML,
    )


@router.message(BotStates.waiting_do_confirm, F.text == BTN_BACK)
async def confirm_do_back(message: Message, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    await answer_text(
        message,
        MSG_CANCELLED,
        reply_markup=keyboard_for_message(settings, message),
        parse_mode=ParseMode.HTML,
    )


@router.message(BotStates.waiting_do_confirm, F.text)
async def confirm_do_unknown(message: Message) -> None:
    await answer_text(
        message,
        MSG_DO_USE_BUTTONS,
        reply_markup=do_confirm_keyboard(),
        parse_mode=ParseMode.HTML,
    )
