from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

BTN_EMM = "Счёт ЕММ"
BTN_TO = "Счёт ТО"
BTN_INFO = "Инфо ТТ"
BTN_REGIONS = "Передать регионы"
BTN_HELP = "Помощь"
BTN_CANCEL = "Отмена"
BTN_MENU = "Меню"
BTN_DONE = "Готово"

MAIN_BUTTONS = {BTN_EMM, BTN_TO, BTN_INFO, BTN_REGIONS, BTN_HELP, BTN_MENU}
CANCEL_BUTTONS = {BTN_CANCEL, BTN_MENU}
FLOW_BUTTONS = MAIN_BUTTONS | {BTN_DONE}


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_EMM), KeyboardButton(text=BTN_TO)],
            [KeyboardButton(text=BTN_INFO), KeyboardButton(text=BTN_REGIONS)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def projects_keyboard(options: list[str]) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = []
    row: list[KeyboardButton] = []
    for option in options:
        row.append(KeyboardButton(text=option))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([KeyboardButton(text=BTN_DONE), KeyboardButton(text=BTN_CANCEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL), KeyboardButton(text=BTN_MENU)]],
        resize_keyboard=True,
    )


def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()
