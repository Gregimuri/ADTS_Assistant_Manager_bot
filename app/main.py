from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import sys

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from app.config import Settings, get_settings
from app.handlers import (
    do_report_router,
    emm_invoice_router,
    info_tt_router,
    menu_router,
    start_router,
    to_invoice_router,
)
from app.services.catalog import Catalog
from app.services.do_report import run_do_report_scheduler
from app.services.sheets import SheetsClient

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhook"

BOT_COMMANDS = [
    BotCommand(command="start", description="Меню и справка"),
    BotCommand(command="help", description="Как пользоваться ботом"),
    BotCommand(command="menu", description="Показать кнопки меню"),
]


async def _setup_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(BOT_COMMANDS)
    await bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeAllGroupChats())


def _configure_logging() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def _webhook_secret(bot_token: str) -> str:
    return hashlib.sha256(bot_token.encode()).hexdigest()


def _build_dispatcher(catalog: Catalog, settings: Settings) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_routers(
        start_router,
        do_report_router,
        menu_router,
        emm_invoice_router,
        to_invoice_router,
        info_tt_router,
    )
    dp.workflow_data.update(catalog=catalog, settings=settings)
    return dp


async def _health(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def _run_web(bot: Bot, dp: Dispatcher, settings: Settings) -> None:
    app = web.Application()
    app.router.add_get("/health", _health)
    app.router.add_get("/", _health)

    public_url = settings.public_base_url
    if public_url:
        secret = _webhook_secret(settings.bot_token)
        webhook_url = f"{public_url}{WEBHOOK_PATH}"
        SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret).register(
            app,
            path=WEBHOOK_PATH,
        )
        setup_application(app, dp, bot=bot)

        async def set_webhook(_app: web.Application) -> None:
            await _setup_bot_commands(bot)
            await bot.set_webhook(
                webhook_url,
                secret_token=secret,
                drop_pending_updates=True,
                allowed_updates=["message", "edited_message", "callback_query"],
            )
            logger.info("Webhook set to %s", webhook_url)

        app.on_startup.append(set_webhook)
    else:
        logger.warning("PORT is set, but public URL is missing; health checks only")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.port)
    await site.start()
    logger.info("HTTP server listening on 0.0.0.0:%s", settings.port)

    try:
        if public_url:
            await asyncio.Event().wait()
        else:
            await dp.start_polling(
                bot,
                drop_pending_updates=True,
                allowed_updates=["message", "edited_message", "callback_query"],
            )
    finally:
        await runner.cleanup()
        await bot.session.close()


async def run() -> None:
    _configure_logging()
    settings = get_settings()
    sheets = SheetsClient(settings)
    catalog = Catalog(sheets)
    bot = Bot(token=settings.bot_token)
    dp = _build_dispatcher(catalog, settings)
    scheduler_task = asyncio.create_task(run_do_report_scheduler(bot, catalog, settings))

    try:
        if settings.port:
            logger.info("Starting web mode for Render")
            await _run_web(bot, dp, settings)
            return

        logger.info("Starting polling mode")
        await _setup_bot_commands(bot)
        await dp.start_polling(
            bot,
            drop_pending_updates=True,
            allowed_updates=["message", "edited_message", "callback_query"],
        )
    finally:
        scheduler_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler_task
        await bot.session.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
