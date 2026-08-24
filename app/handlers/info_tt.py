import logging
import re

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.types import Message

from app.services.catalog import Catalog
from app.services.invoice import INFO_TAG_RE, build_info_reply, join_invoice_blocks, parse_store_names
from app.services.sheets import SheetsError

logger = logging.getLogger(__name__)

router = Router(name="info_tt")

TAG_FILTER = F.text.regexp(re.compile(r"#инфотт", re.IGNORECASE))


@router.message(TAG_FILTER)
async def handle_info_tt(
    message: Message,
    bot: Bot,
    catalog: Catalog,
) -> None:
    lines = parse_store_names(message.text or "", tag_re=INFO_TAG_RE)
    if not lines:
        await message.answer(
            "Укажите названия ТТ, каждое с новой строки после #ИнфоТТ.\n"
            "Можно указать проект первым словом: Фасоль 703961"
        )
        return

    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    try:
        queries = await catalog.parse_info_queries(lines)
        blocks: list[str] = []
        for query in queries:
            matches = await catalog.find_info_stores(query)
            blocks.append(build_info_reply(query.raw, matches))
    except SheetsError:
        logger.exception("Failed to load project sheets")
        await message.answer("Не удалось загрузить справочник проектов. Попробуйте позже.")
        return
    except Exception:
        logger.exception("Failed to build TT info")
        await message.answer("Не удалось найти информацию по ТТ. Попробуйте позже.")
        return

    for chunk in join_invoice_blocks(blocks):
        await message.answer(chunk, parse_mode=ParseMode.HTML)
