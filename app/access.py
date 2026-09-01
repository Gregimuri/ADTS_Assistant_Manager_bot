from __future__ import annotations

from aiogram.types import Message, User

from app.config import Settings
from app.keyboards import main_keyboard


def _user_id(user: User | None) -> int | None:
    return user.id if user else None


def is_admin(settings: Settings, user_id: int | None) -> bool:
    if user_id is None:
        return False
    return user_id in settings.admin_user_ids


def can_use_do_report(settings: Settings, user_id: int | None) -> bool:
    if user_id is None:
        return False
    return user_id in settings.do_report_user_ids


def user_main_keyboard(settings: Settings, user_id: int | None):
    show_do = can_use_do_report(settings, user_id)
    return main_keyboard(show_do=show_do)


def keyboard_for_message(settings: Settings, message: Message):
    return user_main_keyboard(settings, _user_id(message.from_user))
