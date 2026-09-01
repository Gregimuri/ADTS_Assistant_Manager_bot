from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.enums import ChatAction, ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.chat_utils import is_group_chat, reply_markup_for
from app.config import Settings
from app.keyboards import do_confirm_keyboard, main_keyboard
from app.services.catalog import Catalog
from app.services.do_report import build_do_report, send_do_report_chunks
from app.services.invoice import (
    build_info_reply,
    build_invoice_reply,
    build_to_invoice_reply,
    join_invoice_blocks,
)
from app.services.sheets import SheetsError
from app.states import BotStates
from app.texts import (
    GROUP_TAG_HINT,
    MSG_DO_BUILD_ERROR,
    MSG_DO_BUILDING,
    MSG_DO_CONFIRM,
    MSG_DO_SHEET_ERROR,
    MSG_EMM_BUILD_ERROR,
    MSG_EMM_SHEET_ERROR,
    MSG_INFO_BUILD_ERROR,
    MSG_INFO_SHEET_ERROR,
    MSG_NO_TT_NAMES,
    MSG_TO_BUILD_ERROR,
    MSG_TO_SHEET_ERROR,
    PROMPT_EMM,
    PROMPT_INFO,
    PROMPT_TO,
)

logger = logging.getLogger(__name__)


def parse_name_lines(text: str) -> list[str]:
    names: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        names.append(line)
    return names


async def answer_text(
    message: Message,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup=None,
) -> None:
    markup = reply_markup_for(message, reply_markup)
    if is_group_chat(message):
        await message.reply(text, parse_mode=parse_mode, reply_markup=markup)
        return
    await message.answer(text, parse_mode=parse_mode, reply_markup=markup)


async def reply_emm_invoice(
    message: Message,
    bot: Bot,
    catalog: Catalog,
    settings: Settings,
    names: list[str],
) -> None:
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        blocks: list[str] = []
        total = 0
        for name in names:
            matches = await catalog.find_stores(name)
            block, price = build_invoice_reply(name, matches, settings)
            blocks.append(block)
            total += price
    except SheetsError:
        logger.exception("Failed to load EMM sheet")
        await answer_text(
            message,
            MSG_EMM_SHEET_ERROR,
            reply_markup=main_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception:
        logger.exception("Failed to build EMM invoice")
        await answer_text(
            message,
            MSG_EMM_BUILD_ERROR,
            reply_markup=main_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    chunks = join_invoice_blocks(blocks, total=total)
    for index, chunk in enumerate(chunks):
        markup = main_keyboard() if index == len(chunks) - 1 else None
        await answer_text(message, chunk, parse_mode=ParseMode.HTML, reply_markup=markup)


async def reply_to_invoice(
    message: Message,
    bot: Bot,
    catalog: Catalog,
    settings: Settings,
    names: list[str],
) -> None:
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        blocks: list[str] = []
        total = 0
        for name in names:
            matches = await catalog.find_to_visits(name)
            block, price = build_to_invoice_reply(name, matches, settings)
            blocks.append(block)
            total += price
    except SheetsError:
        logger.exception("Failed to load TO sheet")
        await answer_text(
            message,
            MSG_TO_SHEET_ERROR,
            reply_markup=main_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception:
        logger.exception("Failed to build TO invoice")
        await answer_text(
            message,
            MSG_TO_BUILD_ERROR,
            reply_markup=main_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    chunks = join_invoice_blocks(blocks, total=total)
    for index, chunk in enumerate(chunks):
        markup = main_keyboard() if index == len(chunks) - 1 else None
        await answer_text(message, chunk, parse_mode=ParseMode.HTML, reply_markup=markup)


async def reply_info_tt(
    message: Message,
    bot: Bot,
    catalog: Catalog,
    names: list[str],
) -> None:
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        queries = await catalog.parse_info_queries(names)
        blocks: list[str] = []
        for query in queries:
            matches = await catalog.find_info_stores(query)
            blocks.append(build_info_reply(query.raw, matches))
    except SheetsError:
        logger.exception("Failed to load project sheets")
        await answer_text(
            message,
            MSG_INFO_SHEET_ERROR,
            reply_markup=main_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception:
        logger.exception("Failed to build TT info")
        await answer_text(
            message,
            MSG_INFO_BUILD_ERROR,
            reply_markup=main_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    chunks = join_invoice_blocks(blocks)
    for index, chunk in enumerate(chunks):
        markup = main_keyboard() if index == len(chunks) - 1 else None
        await answer_text(message, chunk, parse_mode=ParseMode.HTML, reply_markup=markup)


async def reply_do_report(
    message: Message,
    bot: Bot,
    catalog: Catalog,
    settings: Settings,
    state: FSMContext,
) -> None:
    await answer_text(
        message,
        MSG_DO_BUILDING,
        reply_markup=main_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        count, chunks = await build_do_report(catalog, settings)
    except SheetsError:
        logger.exception("Failed to load DO sheet")
        await answer_text(
            message,
            MSG_DO_SHEET_ERROR,
            reply_markup=main_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception:
        logger.exception("Failed to build DO report")
        await answer_text(
            message,
            MSG_DO_BUILD_ERROR,
            reply_markup=main_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    for chunk in chunks:
        await answer_text(message, chunk, parse_mode=ParseMode.HTML)

    await state.set_state(BotStates.waiting_do_confirm)
    await state.update_data(do_report_chunks=chunks, do_report_count=count)
    await answer_text(
        message,
        MSG_DO_CONFIRM,
        reply_markup=do_confirm_keyboard(),
        parse_mode=ParseMode.HTML,
    )
