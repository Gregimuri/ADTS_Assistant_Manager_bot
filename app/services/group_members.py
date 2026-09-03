from __future__ import annotations

import asyncio
import html
import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_MENTIONS_PER_MESSAGE = 5


@dataclass(frozen=True, slots=True)
class GroupMember:
    user_id: int
    full_name: str
    username: str = ""
    is_bot: bool = False


class GroupMemberStore:
    """Хранит известных участников групп (Bot API не отдаёт полный список)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()

    async def remember(
        self,
        chat_id: int,
        *,
        user_id: int,
        full_name: str,
        username: str = "",
        is_bot: bool = False,
    ) -> None:
        if user_id <= 0:
            return
        async with self._lock:
            data = self._read()
            chats = data.setdefault("chats", {})
            chat_key = str(chat_id)
            members = chats.setdefault(chat_key, {})
            members[str(user_id)] = {
                "full_name": (full_name or f"id{user_id}").strip() or f"id{user_id}",
                "username": (username or "").strip().lstrip("@"),
                "is_bot": bool(is_bot),
            }
            self._write(data)

    async def remove(self, chat_id: int, user_id: int) -> None:
        async with self._lock:
            data = self._read()
            members = data.get("chats", {}).get(str(chat_id))
            if not members:
                return
            if str(user_id) in members:
                del members[str(user_id)]
                self._write(data)

    async def list_members(self, chat_id: int) -> list[GroupMember]:
        async with self._lock:
            data = self._read()
            raw = data.get("chats", {}).get(str(chat_id), {})
        members: list[GroupMember] = []
        for user_id_raw, payload in raw.items():
            try:
                user_id = int(user_id_raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            members.append(
                GroupMember(
                    user_id=user_id,
                    full_name=str(payload.get("full_name") or f"id{user_id}"),
                    username=str(payload.get("username") or ""),
                    is_bot=bool(payload.get("is_bot")),
                )
            )
        members.sort(key=lambda item: item.full_name.casefold())
        return members

    def _read(self) -> dict:
        if not self._path.exists():
            return {"chats": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("Failed to read group members from %s", self._path)
            return {"chats": {}}
        if not isinstance(data, dict):
            return {"chats": {}}
        data.setdefault("chats", {})
        return data

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def format_mention(member: GroupMember) -> str:
    name = html.escape(member.full_name or f"id{member.user_id}")
    # @username надёжнее доставляет уведомление; без username — deep-link по id
    if member.username:
        return f"@{html.escape(member.username)}"
    return f'<a href="tg://user?id={member.user_id}">{name}</a>'


def chunk_mentions(members: list[GroupMember], *, size: int = _MENTIONS_PER_MESSAGE) -> list[str]:
    chunks: list[str] = []
    batch: list[str] = []
    for member in members:
        batch.append(format_mention(member))
        if len(batch) >= size:
            chunks.append(" ".join(batch))
            batch = []
    if batch:
        chunks.append(" ".join(batch))
    return chunks
