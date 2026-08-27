from __future__ import annotations

import logging
import re

from aiogram import Bot, Router
from aiogram.enums import MessageEntityType
from aiogram.filters import Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.config import Settings
from app.handlers.flows import reply_do_report
from app.services.catalog import Catalog

logger = logging.getLogger(__name__)

router = Router(name="do_report")

_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)
_DO_LINE_RE = re.compile(r"^#до(?:\s|$)", re.IGNORECASE)


def _normalize_tag_text(text: str) -> str:
    return text.translate(_INVISIBLE).replace("\xa0", " ").strip()


class DoTagFilter(Filter):
    """Срабатывает на сообщение, где первая строка — хэштег #ДО."""

    async def __call__(self, message: Message) -> bool:
        text = message.text or message.caption or ""
        if not text:
            return False

        normalized = _normalize_tag_text(text)
        if not normalized:
            return False

        first_line = normalized.splitlines()[0].strip()
        if first_line.casefold() == "#до":
            return True
        if _DO_LINE_RE.match(first_line) and not first_line[3:].strip():
            return True

        source = message.text or message.caption or ""
        for entity in message.entities or message.caption_entities or ():
            if entity.type != MessageEntityType.HASHTAG:
                continue
            frag = _normalize_tag_text(source[entity.offset : entity.offset + entity.length])
            if frag.casefold() == "#до":
                return True
        return False


@router.message(DoTagFilter())
async def handle_do_tag(
    message: Message,
    bot: Bot,
    catalog: Catalog,
    settings: Settings,
    state: FSMContext,
) -> None:
    logger.info(
        "Matched #ДО from chat_id=%s user_id=%s text=%r",
        message.chat.id,
        message.from_user.id if message.from_user else None,
        message.text,
    )
    await state.clear()
    await reply_do_report(message, bot, catalog, settings)
