import logging
import re

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.types import Message

from app.config import Settings
from app.services.catalog import Catalog
from app.services.invoice import (
    build_invoice_reply,
    join_invoice_blocks,
    parse_store_names,
)
from app.services.sheets import SheetsError

logger = logging.getLogger(__name__)

router = Router(name="emm_invoice")

TAG_FILTER = F.text.regexp(re.compile(r"#счетемм", re.IGNORECASE))


@router.message(TAG_FILTER)
async def handle_emm_invoice(
    message: Message,
    bot: Bot,
    catalog: Catalog,
    settings: Settings,
) -> None:
    names = parse_store_names(message.text or "")
    if not names:
        await message.answer("Укажите названия ТТ, каждое с новой строки после #СчетЕММ.")
        return

    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    try:
        blocks: list[str] = []
        for name in names:
            matches = await catalog.find_stores(name)
            blocks.append(build_invoice_reply(name, matches, settings))
    except SheetsError:
        logger.exception("Failed to load EMM sheet")
        await message.answer("Не удалось загрузить таблицу ЕММ. Попробуйте позже.")
        return
    except Exception:
        logger.exception("Failed to build EMM invoice")
        await message.answer("Не удалось сформировать счёт. Попробуйте позже.")
        return

    for chunk in join_invoice_blocks(blocks):
        await message.answer(chunk, parse_mode=ParseMode.HTML)
