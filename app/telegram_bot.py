from __future__ import annotations

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from app.config import Settings

# Официальный Bot API не отдаёт боту файлы крупнее ~20 МБ.
TELEGRAM_BOT_DOWNLOAD_LIMIT = 20 * 1024 * 1024


def create_bot(settings: Settings) -> Bot:
    base = settings.telegram_local_api_url.strip()
    if not base:
        return Bot(token=settings.bot_token)
    api = TelegramAPIServer.from_base_url(base.rstrip("/"))
    session = AiohttpSession(api=api)
    return Bot(token=settings.bot_token, session=session)


def large_files_supported(settings: Settings) -> bool:
    return bool(settings.telegram_local_api_url.strip())
