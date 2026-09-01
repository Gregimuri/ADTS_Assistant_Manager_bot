import logging
import re

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.chat_utils import is_group_chat
from app.config import Settings
from app.handlers.flows import answer_text, parse_name_lines, reply_emm_invoice
from app.keyboards import CANCEL_BUTTONS, FLOW_BUTTONS, cancel_keyboard
from app.services.catalog import Catalog
from app.services.invoice import EMM_TAG_RE, parse_store_names
from app.states import BotStates
from app.texts import GROUP_TAG_HINT, MSG_NO_TT_NAMES, PROMPT_EMM

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
        if is_group_chat(message):
            await state.clear()
            await answer_text(message, GROUP_TAG_HINT.format(tag="#СчетЕММ"), parse_mode="HTML")
            return
        await state.set_state(BotStates.waiting_emm)
        await answer_text(message, PROMPT_EMM, reply_markup=cancel_keyboard(), parse_mode="HTML")
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
    if text in FLOW_BUTTONS:
        return
    names = parse_name_lines(text)
    if not names:
        await answer_text(
            message,
            MSG_NO_TT_NAMES,
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )
        return
    await state.clear()
    await reply_emm_invoice(message, bot, catalog, settings, names)
