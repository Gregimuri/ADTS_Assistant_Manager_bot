import logging
import re

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.config import Settings
from app.handlers.flows import reply_do_report
from app.services.catalog import Catalog

logger = logging.getLogger(__name__)

router = Router(name="do_report")

TAG_FILTER = F.text.regexp(re.compile(r"(?i)^\s*#до(?!\w)\s*$"))


@router.message(TAG_FILTER)
async def handle_do_tag(
    message: Message,
    bot: Bot,
    catalog: Catalog,
    settings: Settings,
    state: FSMContext,
) -> None:
    await state.clear()
    await reply_do_report(message, bot, catalog, settings)
