from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

BTN_EMM = "Счёт ЕММ"
BTN_TO = "Счёт ТО"
BTN_INFO = "Инфо ТТ"
BTN_HELP = "Помощь"
BTN_CANCEL = "Отмена"
BTN_MENU = "Меню"

MAIN_BUTTONS = {BTN_EMM, BTN_TO, BTN_INFO, BTN_HELP, BTN_MENU}
CANCEL_BUTTONS = {BTN_CANCEL, BTN_MENU}


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_EMM), KeyboardButton(text=BTN_TO)],
            [KeyboardButton(text=BTN_INFO), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL), KeyboardButton(text=BTN_MENU)]],
        resize_keyboard=True,
    )


def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()
