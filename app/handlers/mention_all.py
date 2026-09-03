from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.types import ChatMemberUpdated, Message, TelegramObject, User

from app.chat_utils import is_group_chat
from app.services.group_members import GroupMember, GroupMemberStore, chunk_mentions

logger = logging.getLogger(__name__)

router = Router(name="mention_all")

_ALL_RE = re.compile(r"(?<![\w@])@all(?!\w)", re.IGNORECASE)
_ACTIVE_STATUSES = {
    ChatMemberStatus.CREATOR,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.RESTRICTED,
}


def _user_full_name(user: User) -> str:
    parts = [user.first_name or "", user.last_name or ""]
    name = " ".join(part for part in parts if part).strip()
    if name:
        return name
    if user.username:
        return user.username
    return f"id{user.id}"


async def _remember_user(store: GroupMemberStore, chat_id: int, user: User | None) -> None:
    if user is None or user.is_bot:
        return
    await store.remember(
        chat_id,
        user_id=user.id,
        full_name=_user_full_name(user),
        username=user.username or "",
        is_bot=False,
    )


def message_has_all_mention(message: Message) -> bool:
    text = message.text or message.caption or ""
    if _ALL_RE.search(text):
        return True
    for entity in message.entities or message.caption_entities or []:
        if entity.type == "mention":
            fragment = text[entity.offset : entity.offset + entity.length]
            if fragment.casefold() == "@all":
                return True
    return False


class GroupMemberTrackerMiddleware(BaseMiddleware):
    def __init__(self, store: GroupMemberStore) -> None:
        self._store = store

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and is_group_chat(event):
            await _remember_user(self._store, event.chat.id, event.from_user)
            if event.reply_to_message and event.reply_to_message.from_user:
                await _remember_user(
                    self._store,
                    event.chat.id,
                    event.reply_to_message.from_user,
                )
        return await handler(event, data)


@router.chat_member()
async def track_chat_member(
    event: ChatMemberUpdated,
    group_members: GroupMemberStore,
) -> None:
    if event.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return
    user = event.new_chat_member.user
    status = event.new_chat_member.status
    if status in _ACTIVE_STATUSES:
        await _remember_user(group_members, event.chat.id, user)
        return
    if status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
        await group_members.remove(event.chat.id, user.id)


@router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.func(message_has_all_mention))
async def handle_all_mention(
    message: Message,
    bot: Bot,
    group_members: GroupMemberStore,
) -> None:
    chat_id = message.chat.id
    await _remember_user(group_members, chat_id, message.from_user)

    by_id: dict[int, GroupMember] = {}
    for member in await group_members.list_members(chat_id):
        if member.is_bot:
            continue
        by_id[member.user_id] = member

    try:
        administrators = await bot.get_chat_administrators(chat_id)
    except Exception:
        logger.exception("Failed to load administrators for chat_id=%s", chat_id)
        administrators = []

    me = await bot.get_me()
    for admin in administrators:
        user = admin.user
        if user.is_bot or user.id == me.id:
            continue
        by_id[user.id] = GroupMember(
            user_id=user.id,
            full_name=_user_full_name(user),
            username=user.username or "",
            is_bot=False,
        )
        await _remember_user(group_members, chat_id, user)

    if message.from_user and not message.from_user.is_bot:
        by_id.pop(message.from_user.id, None)
    by_id.pop(me.id, None)

    members = sorted(by_id.values(), key=lambda item: item.full_name.casefold())
    if not members:
        await message.reply(
            "Пока некого пинговать: бот ещё не видел участников этой группы.\n"
            "Пусть люди напишут в чат (или сделайте бота админом) — и повторите @all.",
        )
        return

    chunks = chunk_mentions(members)
    for index, chunk in enumerate(chunks):
        text = f"🔔 {chunk}" if index == 0 else chunk
        await message.reply(text, parse_mode=ParseMode.HTML)
