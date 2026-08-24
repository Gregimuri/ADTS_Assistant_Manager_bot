import logging
import re

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.config import Settings
from app.handlers.flows import parse_name_lines, reply_emm_invoice
from app.keyboards import CANCEL_BUTTONS, MAIN_BUTTONS, cancel_keyboard
from app.services.catalog import Catalog
from app.services.invoice import EMM_TAG_RE, parse_store_names
from app.states import BotStates

logger = logging.getLogger(__name__)

router = Router(name="emm_invoice")

TAG_FILTER = F.text.regexp(re.compile(r"#счетемм", re.IGNORECASE))


@router.message(TAG_FILTER)
async def handle_emm_tag(
    message: Message,
    bot: Bot,
    catalog: Catalog,
    settings: Settings,
    state: FSMContext,
) -> None:
    names = parse_store_names(message.text or "", tag_re=EMM_TAG_RE)
    if not names:
        await state.set_state(BotStates.waiting_emm)
        await message.answer(
            "Пришлите названия ТТ — каждое с новой строки.",
            reply_markup=cancel_keyboard(),
        )
        return
    await state.clear()
    await reply_emm_invoice(message, bot, catalog, settings, names)


@router.message(BotStates.waiting_emm, F.text)
async def handle_emm_names(
    message: Message,
    bot: Bot,
    catalog: Catalog,
    settings: Settings,
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
    await reply_emm_invoice(message, bot, catalog, settings, names)
