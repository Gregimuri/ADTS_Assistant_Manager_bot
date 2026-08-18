from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher

from app.config import get_settings
from app.handlers import emm_invoice_router, start_router
from app.services.catalog import Catalog
from app.services.sheets import SheetsClient

logger = logging.getLogger(__name__)


async def run() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    settings = get_settings()
    sheets = SheetsClient(settings)
    catalog = Catalog(sheets)

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.include_routers(start_router, emm_invoice_router)
    dp.workflow_data.update(catalog=catalog, settings=settings)

    logger.info("Starting Assistant Manager bot")
    await dp.start_polling(bot, drop_pending_updates=True)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
