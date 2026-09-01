from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

BTN_EMM = "Счёт ЕММ"
BTN_TO = "Счёт ТО"
BTN_INFO = "Инфо ТТ"
BTN_REGIONS = "Передать регионы"
BTN_DO = "Отчет ДО расходка"
BTN_EXIT_PLAN = "План кол-во выходов"
BTN_EXIT_REPORT = "Отчет кол-во выходов"
BTN_ASSEMBLY = "Сборка расходников"
BTN_ASSEMBLY_REPORT = "Отчет сборка расходников"
BTN_HELP = "Помощь"
BTN_CANCEL = "Отмена"
BTN_MENU = "Меню"
BTN_DO_SEND = "Отправить в группу"
BTN_BACK = "Назад"
BTN_DONE = "Готово"

ADMIN_BUTTONS = {
    BTN_EXIT_PLAN,
    BTN_EXIT_REPORT,
    BTN_ASSEMBLY,
    BTN_ASSEMBLY_REPORT,
}
MAIN_BUTTONS = {
    BTN_EMM,
    BTN_TO,
    BTN_INFO,
    BTN_REGIONS,
    BTN_DO,
    BTN_HELP,
    BTN_MENU,
    *ADMIN_BUTTONS,
}
CANCEL_BUTTONS = {BTN_CANCEL, BTN_MENU, BTN_BACK}
FLOW_BUTTONS = MAIN_BUTTONS | {BTN_DONE, BTN_DO_SEND, BTN_BACK}


def do_confirm_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_DO_SEND), KeyboardButton(text=BTN_BACK)],
        ],
        resize_keyboard=True,
    )


def main_keyboard(*, show_do: bool = False, show_admin: bool = False) -> ReplyKeyboardMarkup:
    keyboard: list[list[KeyboardButton]] = [
        [KeyboardButton(text=BTN_EMM), KeyboardButton(text=BTN_TO)],
        [KeyboardButton(text=BTN_INFO), KeyboardButton(text=BTN_REGIONS)],
    ]
    if show_do:
        keyboard.append([KeyboardButton(text=BTN_DO)])
    if show_admin:
        keyboard.extend(
            [
                [KeyboardButton(text=BTN_EXIT_PLAN), KeyboardButton(text=BTN_EXIT_REPORT)],
                [KeyboardButton(text=BTN_ASSEMBLY), KeyboardButton(text=BTN_ASSEMBLY_REPORT)],
            ]
        )
    keyboard.append([KeyboardButton(text=BTN_HELP)])
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
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
