from __future__ import annotations

from aiogram.enums import ChatType
from aiogram.types import Message, ReplyKeyboardMarkup, ReplyKeyboardRemove


def is_group_chat(message: Message) -> bool:
    return message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}


def reply_markup_for(
    message: Message,
    markup: ReplyKeyboardMarkup | ReplyKeyboardRemove | None,
) -> ReplyKeyboardMarkup | ReplyKeyboardRemove | None:
    """В группах убираем reply-клавиатуру; в личке оставляем кнопки."""
    if is_group_chat(message):
        return ReplyKeyboardRemove()
    return markup
