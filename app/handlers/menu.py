from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import Message

from app.chat_utils import is_group_chat
from app.handlers.flows import PROMPT_EMM, PROMPT_INFO, PROMPT_TO, answer_text
from app.keyboards import BTN_CANCEL, BTN_EMM, BTN_INFO, BTN_MENU, BTN_TO, cancel_keyboard, main_keyboard
from app.states import BotStates

router = Router(name="menu")


@router.message(F.text == BTN_EMM, StateFilter(default_state))
async def start_emm(message: Message, state: FSMContext) -> None:
    if is_group_chat(message):
        await state.clear()
        await answer_text(
            message,
            "В группе используйте хэштег со списком ТТ:\n#СчетЕММ\nНазвание ТТ",
        )
        return
    await state.set_state(BotStates.waiting_emm)
    await answer_text(message, PROMPT_EMM, reply_markup=cancel_keyboard())


@router.message(F.text == BTN_TO, StateFilter(default_state))
async def start_to(message: Message, state: FSMContext) -> None:
    if is_group_chat(message):
        await state.clear()
        await answer_text(
            message,
            "В группе используйте хэштег со списком ТТ:\n#СчетТО\nНазвание ТТ",
        )
        return
    await state.set_state(BotStates.waiting_to)
    await answer_text(message, PROMPT_TO, reply_markup=cancel_keyboard())


@router.message(F.text == BTN_INFO, StateFilter(default_state))
async def start_info(message: Message, state: FSMContext) -> None:
    if is_group_chat(message):
        await state.clear()
        await answer_text(
            message,
            "В группе используйте хэштег со списком ТТ:\n#ИнфоТТ\nНазвание ТТ",
        )
        return
    await state.set_state(BotStates.waiting_info)
    await answer_text(message, PROMPT_INFO, reply_markup=cancel_keyboard())


@router.message(F.text.in_({BTN_CANCEL, BTN_MENU}))
async def cancel_or_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = "Главное меню." if message.text == BTN_MENU else "Отменено."
    await answer_text(message, text, reply_markup=main_keyboard())
