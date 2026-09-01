from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.access import keyboard_for_message
from app.config import Settings
from app.handlers.flows import answer_text
from app.keyboards import BTN_HELP
from app.texts import HELP_TEXT, START_TEXT

router = Router(name="start")


@router.message(CommandStart())
async def handle_start(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    await answer_text(
        message,
        START_TEXT,
        reply_markup=keyboard_for_message(settings, message),
        parse_mode="HTML",
    )


@router.message(Command("help"))
@router.message(Command("menu"))
@router.message(F.text == BTN_HELP)
async def handle_help(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    await answer_text(
        message,
        HELP_TEXT,
        reply_markup=keyboard_for_message(settings, message),
        parse_mode="HTML",
    )
