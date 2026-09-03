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

from pathlib import Path

from app.config import Settings, get_settings
from app.handlers import (
    admin_reports_router,
    do_report_router,
    emm_invoice_router,
    info_tt_router,
    mention_all_router,
    menu_router,
    region_transfer_router,
    start_router,
    to_invoice_router,
)
from app.handlers.mention_all import GroupMemberTrackerMiddleware
from app.services.admin_report_scheduler import run_admin_report_scheduler
from app.services.assembly_reports import AssemblyReportsService
from app.services.catalog import Catalog
from app.services.do_report import run_do_report_scheduler
from app.services.exit_reports import ExitReportsService
from app.services.group_members import GroupMemberStore
from app.services.region_transfer import RegionTransferService
from app.services.report_storage import ReportStorage
from app.services.sheets import SheetsClient

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhook"
ALLOWED_UPDATES = ["message", "edited_message", "callback_query", "chat_member"]

BOT_COMMANDS = [
    BotCommand(command="start", description="Меню и справка"),
    BotCommand(command="help", description="Как пользоваться ботом"),
    BotCommand(command="menu", description="Показать кнопки меню"),
]


async def _setup_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(BOT_COMMANDS)
    await bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeAllGroupChats())


async def _start_polling(bot: Bot, dp: Dispatcher) -> None:
    """Polling несовместим с активным webhook — снимаем его перед стартом."""
    deleted = await bot.delete_webhook(drop_pending_updates=True)
    if deleted:
        logger.info("Removed active Telegram webhook before polling")
    await _setup_bot_commands(bot)
    await dp.start_polling(
        bot,
        drop_pending_updates=True,
        allowed_updates=ALLOWED_UPDATES,
    )


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


def _build_dispatcher(
    catalog: Catalog,
    region_transfer: RegionTransferService,
    exit_reports: ExitReportsService,
    assembly_reports: AssemblyReportsService,
    group_members: GroupMemberStore,
    settings: Settings,
) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(GroupMemberTrackerMiddleware(group_members))
    dp.include_routers(
        mention_all_router,
        start_router,
        admin_reports_router,
        do_report_router,
        region_transfer_router,
        menu_router,
        emm_invoice_router,
        to_invoice_router,
        info_tt_router,
    )
    dp.workflow_data.update(
        catalog=catalog,
        settings=settings,
        region_transfer=region_transfer,
        exit_reports=exit_reports,
        assembly_reports=assembly_reports,
        group_members=group_members,
    )
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
                allowed_updates=ALLOWED_UPDATES,
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
            await _start_polling(bot, dp)
    finally:
        await runner.cleanup()
        await bot.session.close()


async def run() -> None:
    _configure_logging()
    settings = get_settings()
    sheets = SheetsClient(settings)
    catalog = Catalog(sheets)
    region_transfer = RegionTransferService(sheets)
    report_storage = ReportStorage(Path(settings.report_data_path))
    exit_reports = ExitReportsService(sheets, report_storage)
    assembly_reports = AssemblyReportsService(settings)
    group_members = GroupMemberStore(Path(settings.group_members_path))
    bot = Bot(token=settings.bot_token)
    dp = _build_dispatcher(
        catalog,
        region_transfer,
        exit_reports,
        assembly_reports,
        group_members,
        settings,
    )
    scheduler_task = asyncio.create_task(run_do_report_scheduler(bot, catalog, settings))
    admin_scheduler_task = asyncio.create_task(
        run_admin_report_scheduler(bot, exit_reports, assembly_reports, settings)
    )

    try:
        if settings.port:
            logger.info("Starting web mode for Render")
            await _run_web(bot, dp, settings)
            return

        logger.info("Starting polling mode")
        await _start_polling(bot, dp)
    finally:
        admin_scheduler_task.cancel()
        scheduler_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await admin_scheduler_task
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler_task
        await bot.session.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
