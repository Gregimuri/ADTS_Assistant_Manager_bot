from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.handlers.flows import answer_text
from app.keyboards import BTN_HELP, main_keyboard

router = Router(name="start")

HELP_TEXT = (
    "Помощник менеджера\n\n"
    "Выберите действие кнопкой внизу или отправьте хэштег со списком ТТ.\n\n"
    "Счёт ЕММ — расчёт выезда по листу ЕММ\n"
    "Счёт ТО — счёт по листу ТО и Bitrix\n"
    "Инфо ТТ — название, адрес и менеджер по проектам\n"
    "Передать регионы — Excel по проектам, менеджеру и регионам\n"
    "#ДО — список ТТ с листа ДО в служебную группу\n\n"
    "Можно и так:\n"
    "#СчетЕММ\n"
    "Начинатель\n"
    "Сахарозаводчица\n\n"
    "#СчетТО\n"
    "Аптека Методика\n\n"
    "#ИнфоТТ\n"
    "Гарантирование\n"
    "Фасоль 703961\n\n"
    "#ПередатьРегионы\n"
    "Проекты: ММ, МА, ДО\n"
    "Менеджер: Гарpинич Николай\n"
    "Регионы: Астраханская обл., Воронежская обл.\n\n"
    "#ДО\n\n"
    "В группе хэштеги работают так же: ТТ — в том же сообщении под тегом.\n"
    "Бот должен видеть сообщения группы "
    "(админ группы или Group Privacy Disable у @BotFather)."
)


@router.message(CommandStart())
async def handle_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await answer_text(message, HELP_TEXT, reply_markup=main_keyboard())


@router.message(Command("help"))
@router.message(Command("menu"))
@router.message(F.text == BTN_HELP)
async def handle_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    await answer_text(message, HELP_TEXT, reply_markup=main_keyboard())
