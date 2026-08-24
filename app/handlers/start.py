from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router(name="start")

HELP_TEXT = (
    "Помощник менеджера.\n\n"
    "Доступные команды — тег и названия ТТ, каждое с новой строки:\n\n"
    "#СчетЕММ\n"
    "Начинатель\n"
    "Сахарозаводчица\n\n"
    "#СчетТО\n"
    "Аптека Методика\n"
    "Черновицкий МД РзФ\n\n"
    "#ИнфоТТ\n"
    "Гарантирование\n"
    "Фасоль 703961"
)


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    await message.answer(HELP_TEXT)
