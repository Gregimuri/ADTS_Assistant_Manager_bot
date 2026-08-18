from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router(name="start")

HELP_TEXT = (
    "Помощник менеджера.\n\n"
    "Сейчас доступна команда #СчетЕММ — пришлите тег и названия ТТ, "
    "каждое с новой строки:\n\n"
    "#СчетЕММ\n"
    "Начинатель\n"
    "Сахарозаводчица"
)


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    await message.answer(HELP_TEXT)
