import html

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import Message

from app.access import keyboard_for_message
from app.config import Settings
from app.handlers.flows import answer_text
from app.keyboards import BTN_HELP, BTN_PING
from app.services.adts_platform import AdtsPlatformClient, AdtsPlatformError
from app.texts import (
    HELP_TEXT,
    MSG_PING_CHECKING,
    MSG_PING_FAIL,
    MSG_PING_NO_HOOK,
    MSG_PING_OK,
    START_TEXT,
)

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


@router.message(F.text == BTN_PING, StateFilter(default_state))
async def handle_ping(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    client = AdtsPlatformClient(settings)
    if not client.enabled():
        await answer_text(
            message,
            MSG_PING_NO_HOOK,
            reply_markup=keyboard_for_message(settings, message),
            parse_mode="HTML",
        )
        return
    await answer_text(
        message,
        MSG_PING_CHECKING,
        reply_markup=keyboard_for_message(settings, message),
        parse_mode="HTML",
    )
    try:
        info = await client.ping()
    except AdtsPlatformError as exc:
        await answer_text(
            message,
            MSG_PING_FAIL.format(error=html.escape(str(exc))),
            reply_markup=keyboard_for_message(settings, message),
            parse_mode="HTML",
        )
        return
    await answer_text(
        message,
        MSG_PING_OK.format(
            project_id=html.escape(str(info["project_id"])),
            columns=info["columns"],
            points=info["points"],
            fo_column=html.escape(str(info["fo_column"])),
        ),
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
