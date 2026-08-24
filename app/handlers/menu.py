from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.handlers.flows import PROMPT_EMM, PROMPT_INFO, PROMPT_TO
from app.keyboards import BTN_CANCEL, BTN_EMM, BTN_INFO, BTN_MENU, BTN_TO, cancel_keyboard, main_keyboard
from app.states import BotStates

router = Router(name="menu")


@router.message(F.text == BTN_EMM)
async def start_emm(message: Message, state: FSMContext) -> None:
    await state.set_state(BotStates.waiting_emm)
    await message.answer(PROMPT_EMM, reply_markup=cancel_keyboard())


@router.message(F.text == BTN_TO)
async def start_to(message: Message, state: FSMContext) -> None:
    await state.set_state(BotStates.waiting_to)
    await message.answer(PROMPT_TO, reply_markup=cancel_keyboard())


@router.message(F.text == BTN_INFO)
async def start_info(message: Message, state: FSMContext) -> None:
    await state.set_state(BotStates.waiting_info)
    await message.answer(PROMPT_INFO, reply_markup=cancel_keyboard())


@router.message(F.text.in_({BTN_CANCEL, BTN_MENU}))
async def cancel_or_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = "Главное меню." if message.text == BTN_MENU else "Отменено."
    await message.answer(text, reply_markup=main_keyboard())
