import logging
import re

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.types import Message

from app.config import Settings
from app.services.catalog import Catalog
from app.services.invoice import (
    TO_TAG_RE,
    build_to_invoice_reply,
    join_invoice_blocks,
    parse_store_names,
)
from app.services.sheets import SheetsError

logger = logging.getLogger(__name__)

router = Router(name="to_invoice")

TAG_FILTER = F.text.regexp(re.compile(r"#счетто", re.IGNORECASE))


@router.message(TAG_FILTER)
async def handle_to_invoice(
    message: Message,
    bot: Bot,
    catalog: Catalog,
    settings: Settings,
) -> None:
    names = parse_store_names(message.text or "", tag_re=TO_TAG_RE)
    if not names:
        await message.answer("Укажите названия ТТ, каждое с новой строки после #СчетТО.")
        return

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
        await message.answer("Не удалось загрузить таблицу ТО. Попробуйте позже.")
        return
    except Exception:
        logger.exception("Failed to build TO invoice")
        await message.answer("Не удалось сформировать счёт. Попробуйте позже.")
        return

    for chunk in join_invoice_blocks(blocks, total=total):
        await message.answer(chunk, parse_mode=ParseMode.HTML)
