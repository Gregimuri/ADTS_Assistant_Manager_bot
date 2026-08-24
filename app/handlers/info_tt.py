import logging
import re

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.handlers.flows import parse_name_lines, reply_info_tt
from app.keyboards import CANCEL_BUTTONS, MAIN_BUTTONS, cancel_keyboard
from app.services.catalog import Catalog
from app.services.invoice import INFO_TAG_RE, parse_store_names
from app.states import BotStates

logger = logging.getLogger(__name__)

router = Router(name="info_tt")

TAG_FILTER = F.text.regexp(re.compile(r"#инфотт", re.IGNORECASE))


@router.message(TAG_FILTER)
async def handle_info_tag(
    message: Message,
    bot: Bot,
    catalog: Catalog,
    state: FSMContext,
) -> None:
    lines = parse_store_names(message.text or "", tag_re=INFO_TAG_RE)
    if not lines:
        await state.set_state(BotStates.waiting_info)
        await message.answer(
            "Пришлите названия или коды ТТ — каждое с новой строки.\n"
            "Можно указать проект первым словом: Фасоль 703961",
            reply_markup=cancel_keyboard(),
        )
        return
    await state.clear()
    await reply_info_tt(message, bot, catalog, lines)


@router.message(BotStates.waiting_info, F.text)
async def handle_info_names(
    message: Message,
    bot: Bot,
    catalog: Catalog,
    state: FSMContext,
) -> None:
    text = message.text or ""
    if text in MAIN_BUTTONS or text in CANCEL_BUTTONS:
        return
    names = parse_name_lines(text)
    if not names:
        await message.answer(
            "Не вижу названий ТТ. Пришлите список — каждое с новой строки.",
            reply_markup=cancel_keyboard(),
        )
        return
    await state.clear()
    await reply_info_tt(message, bot, catalog, names)
