from __future__ import annotations

import asyncio
import html
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import aiohttp

from app.config import Settings

logger = logging.getLogger(__name__)

_MENTIONS_PER_MESSAGE = 40
_STORE_TASK_TITLE = "[assistant-manager] group_members"
_FLUSH_DELAY_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class GroupMember:
    user_id: int
    full_name: str
    username: str = ""
    is_bot: bool = False


class GroupMemberStore:
    """Хранит участников групп локально и дублирует в Bitrix — чтобы переживать деплой."""

    def __init__(self, path: Path, settings: Settings) -> None:
        self._path = path
        self._settings = settings
        self._lock = asyncio.Lock()
        self._remote_task_id: str | None = None
        self._flush_task: asyncio.Task[None] | None = None
        self._dirty = False

    async def hydrate(self) -> None:
        """Подтягивает сохранённый список из Bitrix, если локальный файл пуст/меньше."""
        local = self._read()
        local_count = _members_count(local)
        remote = await self._load_remote()
        if remote is None:
            if local_count:
                self._dirty = True
                self._schedule_flush()
            return
        remote_count = _members_count(remote)
        if remote_count >= local_count:
            async with self._lock:
                self._write(remote)
            logger.info(
                "Restored group members from Bitrix (%s chats, %s users)",
                len(remote.get("chats", {})),
                remote_count,
            )
        elif local_count:
            self._dirty = True
            self._schedule_flush()

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
            self._dirty = True
        self._schedule_flush()

    async def remove(self, chat_id: int, user_id: int) -> None:
        async with self._lock:
            data = self._read()
            members = data.get("chats", {}).get(str(chat_id))
            if not members:
                return
            if str(user_id) in members:
                del members[str(user_id)]
                self._write(data)
                self._dirty = True
        self._schedule_flush()

    async def list_members(self, chat_id: int) -> list[GroupMember]:
        async with self._lock:
            data = self._read()
            chats = data.get("chats", {})
            raw: dict = {}
            for key in _chat_id_keys(chat_id):
                bucket = chats.get(key)
                if isinstance(bucket, dict):
                    raw.update(bucket)
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

    async def flush(self) -> None:
        async with self._lock:
            if not self._dirty:
                return
            data = self._read()
            self._dirty = False
        await self._save_remote(data)

    def _schedule_flush(self) -> None:
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()

        async def _delayed() -> None:
            try:
                await asyncio.sleep(_FLUSH_DELAY_SECONDS)
                await self.flush()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Failed to flush group members to Bitrix")

        self._flush_task = asyncio.create_task(_delayed())

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
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(self._path)

    async def _load_remote(self) -> dict | None:
        if not self._settings.bitrix_webhook_url.strip():
            return None
        try:
            task = await self._find_store_task()
            if task is None:
                return None
            self._remote_task_id = str(task.get("id") or task.get("ID") or "") or None
            raw = str(task.get("description") or task.get("DESCRIPTION") or "").strip()
            if not raw:
                return {"chats": {}}
            # На случай, если Bitrix обернул JSON в [code]...[/code]
            raw = raw.replace("[code]", "").replace("[/code]", "").strip()
            data = json.loads(raw)
            if not isinstance(data, dict):
                return {"chats": {}}
            data.setdefault("chats", {})
            return data
        except Exception:
            logger.exception("Failed to load group members from Bitrix")
            return None

    async def _save_remote(self, data: dict) -> None:
        if not self._settings.bitrix_webhook_url.strip():
            return
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        try:
            task_id = self._remote_task_id
            if not task_id:
                task = await self._find_store_task()
                if task is not None:
                    task_id = str(task.get("id") or task.get("ID") or "") or None
                    self._remote_task_id = task_id
            if task_id:
                await self._bitrix_call(
                    "tasks.task.update",
                    {
                        "taskId": task_id,
                        "fields": {
                            "DESCRIPTION": payload,
                            "DESCRIPTION_IN_BBCODE": "N",
                        },
                    },
                )
            else:
                result = await self._bitrix_call(
                    "tasks.task.add",
                    {
                        "fields": {
                            "TITLE": _STORE_TASK_TITLE,
                            "DESCRIPTION": payload,
                            "DESCRIPTION_IN_BBCODE": "N",
                            "RESPONSIBLE_ID": self._settings.bitrix_assembly_responsible_id,
                        },
                    },
                )
                task = result.get("task") if isinstance(result, dict) else None
                if isinstance(task, dict):
                    self._remote_task_id = str(task.get("id") or task.get("ID") or "") or None
            logger.info("Saved group members to Bitrix task_id=%s", self._remote_task_id)
        except Exception:
            logger.exception("Failed to save group members to Bitrix")
            async with self._lock:
                self._dirty = True

    async def _find_store_task(self) -> dict[str, Any] | None:
        result = await self._bitrix_call(
            "tasks.task.list",
            {
                "select": ["ID", "TITLE", "DESCRIPTION"],
                "filter": {"TITLE": _STORE_TASK_TITLE},
                "start": 0,
            },
        )
        tasks = []
        if isinstance(result, dict):
            tasks = result.get("tasks") or []
        elif isinstance(result, list):
            tasks = result
        for item in tasks:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("TITLE") or "")
            if title == _STORE_TASK_TITLE:
                return item
        return None

    async def _bitrix_call(self, method: str, params: dict[str, Any]) -> Any:
        base = self._settings.bitrix_webhook_url.rstrip("/") + "/"
        url = urljoin(base, method)
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=_flatten_params(params)) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
        if not isinstance(data, dict):
            raise RuntimeError("Bitrix returned unexpected payload")
        if data.get("error"):
            raise RuntimeError(f"Bitrix API: {data.get('error_description') or data['error']}")
        return data.get("result")


def format_mention(member: GroupMember) -> str:
    name = html.escape(member.full_name or f"id{member.user_id}")
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


def _chat_id_keys(chat_id: int) -> list[str]:
    keys = [str(chat_id)]
    absolute = abs(chat_id)
    as_text = str(absolute)
    if as_text.startswith("100") and len(as_text) > 3:
        keys.append(str(-int(as_text[3:])))
    else:
        keys.append(str(-int(f"100{absolute}")))
    unique: list[str] = []
    for key in keys:
        if key not in unique:
            unique.append(key)
    return unique


def _members_count(data: dict) -> int:
    chats = data.get("chats", {})
    if not isinstance(chats, dict):
        return 0
    total = 0
    for bucket in chats.values():
        if isinstance(bucket, dict):
            total += len(bucket)
    return total


def _flatten_params(params: dict[str, Any], prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in params.items():
        full_key = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, list):
            for index, item in enumerate(value):
                flat[f"{full_key}[{index}]"] = str(item)
        elif isinstance(value, dict):
            flat.update(_flatten_params(value, full_key))
        else:
            flat[full_key] = str(value)
    return flat
