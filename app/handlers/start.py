from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.handlers.flows import answer_text
from app.keyboards import BTN_HELP, main_keyboard
from app.texts import HELP_TEXT, START_TEXT

router = Router(name="start")


@router.message(CommandStart())
async def handle_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await answer_text(message, START_TEXT, reply_markup=main_keyboard(), parse_mode="HTML")


@router.message(Command("help"))
@router.message(Command("menu"))
@router.message(F.text == BTN_HELP)
async def handle_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    await answer_text(message, HELP_TEXT, reply_markup=main_keyboard(), parse_mode="HTML")
