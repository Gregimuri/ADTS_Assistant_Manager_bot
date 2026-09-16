from __future__ import annotations

import logging
from io import BytesIO

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from app.config import Settings
from app.services.final_report import FinalReportError
from app.telegram_bot import TELEGRAM_BOT_DOWNLOAD_LIMIT, large_files_supported

logger = logging.getLogger(__name__)


async def download_telegram_file(
    bot: Bot,
    file_id: str,
    settings: Settings,
    *,
    file_size: int | None = None,
) -> bytes:
    if (
        file_size is not None
        and file_size > TELEGRAM_BOT_DOWNLOAD_LIMIT
        and not large_files_supported(settings)
    ):
        raise FinalReportError(
            "Файл больше 20 МБ. Telegram не отдаёт такие файлы боту через облачный API. "
            "Задайте TELEGRAM_LOCAL_API_URL (локальный Bot API server) на сервере бота "
            "или отправьте видео меньшего размера / с stronger-сжатием."
        )
    try:
        buffer = await bot.download(file_id, timeout=600)
    except TelegramBadRequest as exc:
        message = str(exc).casefold()
        if "file is too big" in message or "too big" in message:
            raise FinalReportError(
                "Файл слишком большой для облачного Bot API (лимит ~20 МБ). "
                "Нужен локальный Telegram Bot API (TELEGRAM_LOCAL_API_URL) на сервере бота."
            ) from exc
        raise FinalReportError(f"Не удалось скачать файл из Telegram: {exc}") from exc
    except Exception as exc:
        raise FinalReportError(f"Не удалось скачать файл из Telegram: {exc}") from exc

    if buffer is None:
        raise FinalReportError("Telegram вернул пустой файл.")
    if isinstance(buffer, BytesIO):
        return buffer.getvalue()
    data = buffer.read()
    if isinstance(data, bytes):
        return data
    raise FinalReportError("Некорректные данные файла из Telegram.")
