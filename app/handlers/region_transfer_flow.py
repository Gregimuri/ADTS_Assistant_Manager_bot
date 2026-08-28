from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot
from aiogram.types import BufferedInputFile, Message

from app.handlers.flows import answer_text
from app.keyboards import main_keyboard
from app.services.region_transfer import (
    RegionTransferRequest,
    RegionTransferService,
    build_region_transfer_excel,
)
from app.services.sheets import SheetsError

logger = logging.getLogger(__name__)

REGION_TRANSFER_HINT = (
    "В группе пришлите параметры в том же сообщении:\n"
    "#ПередатьРегионы\n"
    "Проекты: ММ, МА, ДО\n"
    "Менеджер: Гарpинич Николай\n"
    "Регионы: Астраханская обл., Воронежская обл."
)


async def reply_region_transfer(
    message: Message,
    bot: Bot,
    service: RegionTransferService,
    request: RegionTransferRequest,
) -> None:
    await answer_text(message, "Формирую Excel…", reply_markup=main_keyboard())
    try:
        result = await service.build_result(request)
    except ValueError as exc:
        await answer_text(message, str(exc), reply_markup=main_keyboard())
        return
    except SheetsError:
        logger.exception("Failed to load project sheets for region transfer")
        await answer_text(
            message,
            "Не удалось загрузить таблицы проектов. Попробуйте позже.",
            reply_markup=main_keyboard(),
        )
        return
    except Exception:
        logger.exception("Failed to build region transfer report")
        await answer_text(
            message,
            "Не удалось сформировать Excel. Попробуйте позже.",
            reply_markup=main_keyboard(),
        )
        return

    if result.total_rows == 0:
        await answer_text(
            message,
            "Подходящих точек не найдено (или все со статусом «Выполнен»).",
            reply_markup=main_keyboard(),
        )
        return

    excel = build_region_transfer_excel(result)
    filename = f"передача_регионов_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    document = BufferedInputFile(excel.read(), filename=filename)
    await bot.send_document(message.chat.id, document)
    await answer_text(
        message,
        f"Готово: {result.total_rows} точек в {len(result.sheets)} листах.",
        reply_markup=main_keyboard(),
    )
