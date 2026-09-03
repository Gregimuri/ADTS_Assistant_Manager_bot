from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, Message, TelegramObject, User

from app.chat_utils import is_group_chat
from app.services.group_members import GroupMember, GroupMemberStore, chunk_mentions

logger = logging.getLogger(__name__)

router = Router(name="mention_all")

# @all / @все / /all — с невидимыми символами и без учёта регистра
_ALL_RE = re.compile(
    r"(?<![\w@])@\s*(?:all|все)(?!\w)",
    re.IGNORECASE,
)
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)
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


def _normalize_trigger_text(value: str) -> str:
    return (value or "").translate(_INVISIBLE).replace("\xa0", " ").strip()


async def _remember_user(store: GroupMemberStore, chat_id: int, user: User | None) -> None:
    if user is None or user.is_bot:
        return
    try:
        await store.remember(
            chat_id,
            user_id=user.id,
            full_name=_user_full_name(user),
            username=user.username or "",
            is_bot=False,
        )
    except Exception:
        logger.exception("Failed to remember user_id=%s in chat_id=%s", user.id, chat_id)


def message_has_all_mention(message: Message) -> bool:
    raw = message.text or message.caption or ""
    text = _normalize_trigger_text(raw)
    if _ALL_RE.search(text):
        return True
    for entity in message.entities or message.caption_entities or []:
        if entity.type != "mention":
            continue
        # offset в UTF-16 code units относительно исходного текста Telegram
        utf16 = raw.encode("utf-16-le")
        start = entity.offset * 2
        end = (entity.offset + entity.length) * 2
        if start < 0 or end > len(utf16):
            continue
        fragment = _normalize_trigger_text(utf16[start:end].decode("utf-16-le"))
        if fragment.casefold() in {"@all", "@все"}:
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
        try:
            if isinstance(event, Message) and is_group_chat(event):
                await _remember_user(self._store, event.chat.id, event.from_user)
                if event.reply_to_message and event.reply_to_message.from_user:
                    await _remember_user(
                        self._store,
                        event.chat.id,
                        event.reply_to_message.from_user,
                    )
        except Exception:
            logger.exception("Group member tracker middleware failed")
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
        try:
            await group_members.remove(event.chat.id, user.id)
        except Exception:
            logger.exception("Failed to remove user_id=%s from chat_id=%s", user.id, event.chat.id)


async def _collect_members(
    bot: Bot,
    group_members: GroupMemberStore,
    chat_id: int,
    *,
    also_remember: User | None = None,
) -> list[GroupMember]:
    await _remember_user(group_members, chat_id, also_remember)

    by_id: dict[int, GroupMember] = {}
    try:
        for member in await group_members.list_members(chat_id):
            if member.is_bot:
                continue
            by_id[member.user_id] = member
    except Exception:
        logger.exception("Failed to list stored members for chat_id=%s", chat_id)

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

    by_id.pop(me.id, None)
    return sorted(by_id.values(), key=lambda item: item.full_name.casefold())


async def _reply_all_mentions(
    message: Message,
    bot: Bot,
    group_members: GroupMemberStore,
) -> None:
    if not is_group_chat(message):
        await message.answer("Команда @all /all работает только в группах.")
        return

    try:
        members = await _collect_members(
            bot,
            group_members,
            message.chat.id,
            also_remember=message.from_user,
        )
        if not members:
            await message.reply(
                "Пока некого пинговать: бот ещё не видел участников этой группы.\n\n"
                "Что сделать:\n"
                "1) Сделайте бота администратором группы\n"
                "2) В @BotFather отключите Privacy Mode (/setprivacy → Disable)\n"
                "3) Пусть участники напишут что-нибудь в чат\n"
                "4) Повторите /all или @all",
            )
            return

        chunks = chunk_mentions(members)
        for index, chunk in enumerate(chunks):
            text = f"🔔 Все ({len(members)}): {chunk}" if index == 0 else chunk
            await message.reply(text, parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception("Failed to handle @all in chat_id=%s", message.chat.id)
        await message.reply("Не удалось собрать упоминания. Проверьте, что бот админ группы.")


@router.message(Command("all"))
async def handle_all_command(
    message: Message,
    bot: Bot,
    group_members: GroupMemberStore,
) -> None:
    await _reply_all_mentions(message, bot, group_members)


@router.message(
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP, "group", "supergroup"}),
    F.func(message_has_all_mention),
)
async def handle_all_mention(
    message: Message,
    bot: Bot,
    group_members: GroupMemberStore,
) -> None:
    await _reply_all_mentions(message, bot, group_members)
