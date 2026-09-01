from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import Message

from app.chat_utils import is_group_chat
from app.handlers.flows import answer_text
from app.texts import PROMPT_EMM, PROMPT_INFO, PROMPT_TO
from app.keyboards import BTN_CANCEL, BTN_EMM, BTN_INFO, BTN_MENU, BTN_TO, cancel_keyboard, main_keyboard
from app.states import BotStates
from app.texts import MSG_CANCELLED, MSG_GROUP_USE_HASHTAG, MSG_MAIN_MENU

router = Router(name="menu")


@router.message(F.text == BTN_EMM, StateFilter(default_state))
async def start_emm(message: Message, state: FSMContext) -> None:
    if is_group_chat(message):
        await state.clear()
        await answer_text(
            message,
            MSG_GROUP_USE_HASHTAG.format(example="#СчетЕММ\nНазвание ТТ"),
            parse_mode="HTML",
        )
        return
    await state.set_state(BotStates.waiting_emm)
    await answer_text(message, PROMPT_EMM, reply_markup=cancel_keyboard(), parse_mode="HTML")


@router.message(F.text == BTN_TO, StateFilter(default_state))
async def start_to(message: Message, state: FSMContext) -> None:
    if is_group_chat(message):
        await state.clear()
        await answer_text(
            message,
            MSG_GROUP_USE_HASHTAG.format(example="#СчетТО\nНазвание ТТ"),
            parse_mode="HTML",
        )
        return
    await state.set_state(BotStates.waiting_to)
    await answer_text(message, PROMPT_TO, reply_markup=cancel_keyboard(), parse_mode="HTML")


@router.message(F.text == BTN_INFO, StateFilter(default_state))
async def start_info(message: Message, state: FSMContext) -> None:
    if is_group_chat(message):
        await state.clear()
        await answer_text(
            message,
            MSG_GROUP_USE_HASHTAG.format(example="#ИнфоТТ\nНазвание ТТ"),
            parse_mode="HTML",
        )
        return
    await state.set_state(BotStates.waiting_info)
    await answer_text(message, PROMPT_INFO, reply_markup=cancel_keyboard(), parse_mode="HTML")


@router.message(F.text.in_({BTN_CANCEL, BTN_MENU}))
async def cancel_or_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = MSG_MAIN_MENU if message.text == BTN_MENU else MSG_CANCELLED
    await answer_text(message, text, reply_markup=main_keyboard(), parse_mode="HTML")
