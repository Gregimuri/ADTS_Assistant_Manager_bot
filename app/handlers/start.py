from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.keyboards import BTN_HELP, main_keyboard

router = Router(name="start")

HELP_TEXT = (
    "Помощник менеджера\n\n"
    "Выберите действие кнопкой внизу или отправьте хэштег со списком ТТ.\n\n"
    "Счёт ЕММ — расчёт выезда по листу ЕММ\n"
    "Счёт ТО — счёт по листу ТО и Bitrix\n"
    "Инфо ТТ — название, адрес и менеджер по проектам\n\n"
    "Можно и так:\n"
    "#СчетЕММ\n"
    "Начинатель\n"
    "Сахарозаводчица\n\n"
    "#СчетТО\n"
    "Аптека Методика\n\n"
    "#ИнфоТТ\n"
    "Гарантирование\n"
    "Фасоль 703961"
)


@router.message(CommandStart())
async def handle_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(HELP_TEXT, reply_markup=main_keyboard())


@router.message(Command("help"))
@router.message(Command("menu"))
@router.message(F.text == BTN_HELP)
async def handle_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(HELP_TEXT, reply_markup=main_keyboard())
