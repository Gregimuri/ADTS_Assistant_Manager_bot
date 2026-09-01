from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile, Message

from app.handlers.flows import answer_text
from app.keyboards import main_keyboard
from app.services.region_transfer import (
    RegionTransferRequest,
    RegionTransferService,
    build_region_transfer_excel,
)
from app.services.sheets import SheetsError
from app.texts import (
    MSG_REGIONS_BUILDING,
    MSG_REGIONS_BUILD_ERROR,
    MSG_REGIONS_DONE,
    MSG_REGIONS_EMPTY,
    MSG_REGIONS_SHEET_ERROR,
)

logger = logging.getLogger(__name__)


async def reply_region_transfer(
    message: Message,
    bot: Bot,
    service: RegionTransferService,
    request: RegionTransferRequest,
) -> None:
    await answer_text(
        message,
        MSG_REGIONS_BUILDING,
        reply_markup=main_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    try:
        result = await service.build_result(request)
    except ValueError as exc:
        await answer_text(message, str(exc), reply_markup=main_keyboard(), parse_mode=ParseMode.HTML)
        return
    except SheetsError:
        logger.exception("Failed to load project sheets for region transfer")
        await answer_text(
            message,
            MSG_REGIONS_SHEET_ERROR,
            reply_markup=main_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception:
        logger.exception("Failed to build region transfer report")
        await answer_text(
            message,
            MSG_REGIONS_BUILD_ERROR,
            reply_markup=main_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    if result.total_rows == 0:
        await answer_text(
            message,
            MSG_REGIONS_EMPTY,
            reply_markup=main_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    excel = build_region_transfer_excel(result)
    filename = f"передача_регионов_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    document = BufferedInputFile(excel.read(), filename=filename)
    await bot.send_document(message.chat.id, document)
    await answer_text(
        message,
        MSG_REGIONS_DONE.format(count=result.total_rows, sheets=len(result.sheets)),
        reply_markup=main_keyboard(),
        parse_mode=ParseMode.HTML,
    )
