from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.enums import ChatAction, ParseMode
from aiogram.types import Message

from app.chat_utils import is_group_chat, reply_markup_for
from app.config import Settings
from app.keyboards import main_keyboard
from app.services.catalog import Catalog
from app.services.invoice import (
    build_do_report_blocks,
    build_info_reply,
    build_invoice_reply,
    build_to_invoice_reply,
    join_invoice_blocks,
)
from app.services.sheets import SheetsError

logger = logging.getLogger(__name__)

PROMPT_EMM = (
    "Счёт ЕММ\n\n"
    "Пришлите названия ТТ — каждое с новой строки.\n"
    "Пример:\n"
    "Начинатель\n"
    "Сахарозаводчица"
)

PROMPT_TO = (
    "Счёт ТО\n\n"
    "Пришлите названия ТТ — каждое с новой строки.\n"
    "Пример:\n"
    "Аптека Методика\n"
    "Черновицкий МД РзФ"
)

PROMPT_INFO = (
    "Инфо ТТ\n\n"
    "Пришлите названия или коды ТТ — каждое с новой строки.\n"
    "Можно указать проект первым словом.\n"
    "Пример:\n"
    "Гарантирование\n"
    "Фасоль 703961"
)

GROUP_TAG_HINT = (
    "В группе пришлите названия ТТ в том же сообщении под хэштегом.\n"
    "Пример:\n"
    "{tag}\n"
    "Начинатель\n"
    "Сахарозаводчица"
)


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
            "Не удалось загрузить таблицу ЕММ. Попробуйте позже.",
            reply_markup=main_keyboard(),
        )
        return
    except Exception:
        logger.exception("Failed to build EMM invoice")
        await answer_text(
            message,
            "Не удалось сформировать счёт. Попробуйте позже.",
            reply_markup=main_keyboard(),
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
            "Не удалось загрузить таблицу ТО. Попробуйте позже.",
            reply_markup=main_keyboard(),
        )
        return
    except Exception:
        logger.exception("Failed to build TO invoice")
        await answer_text(
            message,
            "Не удалось сформировать счёт. Попробуйте позже.",
            reply_markup=main_keyboard(),
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
            "Не удалось загрузить справочник проектов. Попробуйте позже.",
            reply_markup=main_keyboard(),
        )
        return
    except Exception:
        logger.exception("Failed to build TT info")
        await answer_text(
            message,
            "Не удалось найти информацию по ТТ. Попробуйте позже.",
            reply_markup=main_keyboard(),
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
) -> None:
    await answer_text(message, "Собираю отчёт #ДО…", reply_markup=main_keyboard())
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        matches = await catalog.find_do_report_stores(settings)
        chunks = build_do_report_blocks(matches)
    except SheetsError:
        logger.exception("Failed to load DO sheet")
        await answer_text(
            message,
            "Не удалось загрузить лист ДО. Попробуйте позже.",
            reply_markup=main_keyboard(),
        )
        return
    except Exception:
        logger.exception("Failed to build DO report")
        await answer_text(
            message,
            "Не удалось сформировать отчёт ДО. Попробуйте позже.",
            reply_markup=main_keyboard(),
        )
        return

    if not chunks:
        await answer_text(
            message,
            "Подходящих ТТ по #ДО сейчас нет.",
            reply_markup=main_keyboard(),
        )
        return

    chat_ids = _do_chat_id_candidates(settings.do_report_chat_id)
    last_error: Exception | None = None
    sent = False
    for chat_id in chat_ids:
        try:
            for chunk in chunks:
                await bot.send_message(chat_id, chunk, parse_mode=ParseMode.HTML)
            logger.info("DO report sent to chat_id=%s (%s TT)", chat_id, len(matches))
            sent = True
            break
        except Exception as exc:  # noqa: BLE001 — пробуем запасной chat_id
            last_error = exc
            logger.warning("Failed to send DO report to chat_id=%s: %s", chat_id, exc)

    if not sent:
        logger.error(
            "Failed to send DO report to any of %s: %s",
            chat_ids,
            last_error,
            exc_info=last_error,
        )
        await answer_text(
            message,
            "Не удалось отправить отчёт в группу. "
            "Проверьте chat_id и что бот добавлен в чат.",
            reply_markup=main_keyboard(),
        )
        return

    await answer_text(
        message,
        f"Отправлено в группу: {len(matches)} ТТ.",
        reply_markup=main_keyboard(),
    )


def _do_chat_id_candidates(chat_id: int) -> list[int]:
    """Пробуем указанный id и типичный вариант с префиксом -100 для супергрупп."""
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
