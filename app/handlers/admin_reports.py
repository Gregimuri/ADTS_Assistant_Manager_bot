from __future__ import annotations

import logging
import re

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction, MessageEntityType, ParseMode
from aiogram.filters import Filter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import Message

from app.access import is_admin, keyboard_for_message
from app.chat_utils import is_group_chat
from app.config import Settings
from app.handlers.flows import answer_text
from app.keyboards import (
    BTN_ASSEMBLY,
    BTN_ASSEMBLY_REPORT,
    BTN_BACK,
    BTN_DONE,
    BTN_EXIT_PLAN,
    BTN_EXIT_REPORT,
    BTN_MENU,
    BTN_DO_SEND,
    CANCEL_BUTTONS,
    do_confirm_keyboard,
    projects_keyboard,
)
from app.services.assembly_reports import AssemblyReportsService, BitrixTasksError
from app.services.exit_reports import (
    ExitReportsService,
    parse_exit_plan_message,
    parse_exit_report_message,
)
from app.services.report_delivery import send_report_text
from app.services.sheets import SheetsError
from app.states import BotStates
from app.texts import (
    MSG_ADMIN_ACCESS_DENIED,
    MSG_ADMIN_BITRIX_ERROR,
    MSG_ADMIN_BUILD_ERROR,
    MSG_ADMIN_BUILDING,
    MSG_ADMIN_CONFIRM,
    MSG_ADMIN_SEND_ERROR,
    MSG_ADMIN_SENT,
    MSG_ADMIN_SHEET_ERROR,
    MSG_CANCELLED,
    MSG_EXIT_PLAN_GROUP_HINT,
    MSG_EXIT_PLAN_PROJECTS,
    MSG_EXIT_REPORT_GROUP_HINT,
    MSG_EXIT_REPORT_PROJECTS,
    MSG_MAIN_MENU,
    MSG_REGIONS_NEED_PROJECT,
    MSG_REGIONS_PICK_PROJECT,
    MSG_REGIONS_PROJECTS_UPDATED,
    MSG_DO_USE_BUTTONS,
)

logger = logging.getLogger(__name__)

router = Router(name="admin_reports")

_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)


def _normalize_tag_text(text: str) -> str:
    return text.translate(_INVISIBLE).replace("\xa0", " ").strip()


def _user_id(message: Message) -> int | None:
    return message.from_user.id if message.from_user else None


async def _deny_admin_access(message: Message, settings: Settings) -> None:
    if is_group_chat(message):
        return
    await answer_text(
        message,
        MSG_ADMIN_ACCESS_DENIED,
        reply_markup=keyboard_for_message(settings, message),
        parse_mode=ParseMode.HTML,
    )


async def _preview_report(
    message: Message,
    state: FSMContext,
    settings: Settings,
    text: str,
    *,
    extra_text: str | None = None,
) -> None:
    await answer_text(message, text)
    if extra_text:
        await answer_text(message, extra_text)
    await state.set_state(BotStates.waiting_admin_report_confirm)
    state_data: dict[str, object] = {
        "admin_report_text": text,
        "admin_report_user_id": _user_id(message),
    }
    if extra_text:
        state_data["admin_report_extra_text"] = extra_text
    await state.update_data(**state_data)
    await answer_text(
        message,
        MSG_ADMIN_CONFIRM,
        reply_markup=do_confirm_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def _build_and_preview_exit_plan(
    message: Message,
    bot: Bot,
    state: FSMContext,
    settings: Settings,
    exit_reports: ExitReportsService,
    projects: list[str],
) -> None:
    await answer_text(
        message,
        MSG_ADMIN_BUILDING,
        reply_markup=keyboard_for_message(settings, message),
        parse_mode=ParseMode.HTML,
    )
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        text = await exit_reports.build_exit_plan(projects)
    except ValueError as exc:
        await answer_text(
            message,
            str(exc),
            reply_markup=keyboard_for_message(settings, message),
            parse_mode=ParseMode.HTML,
        )
        return
    except SheetsError:
        logger.exception("Failed to build exit plan")
        await answer_text(
            message,
            MSG_ADMIN_SHEET_ERROR,
            reply_markup=keyboard_for_message(settings, message),
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception:
        logger.exception("Failed to build exit plan")
        await answer_text(
            message,
            MSG_ADMIN_BUILD_ERROR,
            reply_markup=keyboard_for_message(settings, message),
            parse_mode=ParseMode.HTML,
        )
        return
    await state.clear()
    await _preview_report(message, state, settings, text)


async def _build_and_preview_exit_report(
    message: Message,
    bot: Bot,
    state: FSMContext,
    settings: Settings,
    exit_reports: ExitReportsService,
    projects: list[str],
) -> None:
    await answer_text(
        message,
        MSG_ADMIN_BUILDING,
        reply_markup=keyboard_for_message(settings, message),
        parse_mode=ParseMode.HTML,
    )
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        text = await exit_reports.build_exit_report(projects)
        extra_text = await exit_reports.build_do_cumulative_report(
            project=settings.do_sheet_name,
        )
    except ValueError as exc:
        await answer_text(
            message,
            str(exc),
            reply_markup=keyboard_for_message(settings, message),
            parse_mode=ParseMode.HTML,
        )
        return
    except SheetsError:
        logger.exception("Failed to build exit report")
        await answer_text(
            message,
            MSG_ADMIN_SHEET_ERROR,
            reply_markup=keyboard_for_message(settings, message),
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception:
        logger.exception("Failed to build exit report")
        await answer_text(
            message,
            MSG_ADMIN_BUILD_ERROR,
            reply_markup=keyboard_for_message(settings, message),
            parse_mode=ParseMode.HTML,
        )
        return
    await state.clear()
    await _preview_report(message, state, settings, text, extra_text=extra_text)


async def _build_and_preview_assembly(
    message: Message,
    bot: Bot,
    state: FSMContext,
    settings: Settings,
    assembly_reports: AssemblyReportsService,
) -> None:
    await answer_text(
        message,
        MSG_ADMIN_BUILDING,
        reply_markup=keyboard_for_message(settings, message),
        parse_mode=ParseMode.HTML,
    )
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        text = await assembly_reports.build_assembly_snapshot()
    except BitrixTasksError:
        logger.exception("Failed to build assembly snapshot")
        await answer_text(
            message,
            MSG_ADMIN_BITRIX_ERROR,
            reply_markup=keyboard_for_message(settings, message),
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception:
        logger.exception("Failed to build assembly snapshot")
        await answer_text(
            message,
            MSG_ADMIN_BUILD_ERROR,
            reply_markup=keyboard_for_message(settings, message),
            parse_mode=ParseMode.HTML,
        )
        return
    await state.clear()
    await _preview_report(message, state, settings, text)


async def _build_and_preview_assembly_report(
    message: Message,
    bot: Bot,
    state: FSMContext,
    settings: Settings,
    assembly_reports: AssemblyReportsService,
) -> None:
    await answer_text(
        message,
        MSG_ADMIN_BUILDING,
        reply_markup=keyboard_for_message(settings, message),
        parse_mode=ParseMode.HTML,
    )
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        text = await assembly_reports.build_assembly_report()
    except BitrixTasksError:
        logger.exception("Failed to build assembly report")
        await answer_text(
            message,
            MSG_ADMIN_BITRIX_ERROR,
            reply_markup=keyboard_for_message(settings, message),
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception:
        logger.exception("Failed to build assembly report")
        await answer_text(
            message,
            MSG_ADMIN_BUILD_ERROR,
            reply_markup=keyboard_for_message(settings, message),
            parse_mode=ParseMode.HTML,
        )
        return
    await state.clear()
    await _preview_report(message, state, settings, text)


class _AdminTagFilter(Filter):
    def __init__(self, pattern: str) -> None:
        self._pattern = pattern.casefold()

    async def __call__(self, message: Message) -> bool:
        text = message.text or message.caption or ""
        if not text:
            return False
        normalized = _normalize_tag_text(text)
        if not normalized:
            return False
        first_line = normalized.splitlines()[0].strip().casefold()
        if first_line.startswith(self._pattern):
            return True
        source = message.text or message.caption or ""
        for entity in message.entities or message.caption_entities or ():
            if entity.type != MessageEntityType.HASHTAG:
                continue
            frag = _normalize_tag_text(source[entity.offset : entity.offset + entity.length]).casefold()
            if frag.startswith(self._pattern):
                return True
        return False


@router.message(F.text == BTN_EXIT_PLAN, StateFilter(default_state))
async def start_exit_plan_button(
    message: Message,
    state: FSMContext,
    settings: Settings,
    exit_reports: ExitReportsService,
) -> None:
    if not is_admin(settings, _user_id(message)):
        await _deny_admin_access(message, settings)
        return
    if is_group_chat(message):
        await answer_text(message, MSG_EXIT_PLAN_GROUP_HINT, parse_mode=ParseMode.HTML)
        return
    projects = await exit_reports.list_projects()
    await state.set_state(BotStates.exit_plan_projects)
    await state.update_data(
        selected_projects=[],
        available_projects=projects,
        admin_flow="exit_plan",
    )
    await answer_text(
        message,
        MSG_EXIT_PLAN_PROJECTS,
        reply_markup=projects_keyboard(projects),
        parse_mode=ParseMode.HTML,
    )


@router.message(F.text == BTN_EXIT_REPORT, StateFilter(default_state))
async def start_exit_report_button(
    message: Message,
    state: FSMContext,
    settings: Settings,
    exit_reports: ExitReportsService,
) -> None:
    if not is_admin(settings, _user_id(message)):
        await _deny_admin_access(message, settings)
        return
    if is_group_chat(message):
        await answer_text(message, MSG_EXIT_REPORT_GROUP_HINT, parse_mode=ParseMode.HTML)
        return
    projects = await exit_reports.list_projects()
    await state.set_state(BotStates.exit_report_projects)
    await state.update_data(
        selected_projects=[],
        available_projects=projects,
        admin_flow="exit_report",
    )
    await answer_text(
        message,
        MSG_EXIT_REPORT_PROJECTS,
        reply_markup=projects_keyboard(projects),
        parse_mode=ParseMode.HTML,
    )


@router.message(F.text == BTN_ASSEMBLY, StateFilter(default_state))
async def start_assembly_button(
    message: Message,
    bot: Bot,
    state: FSMContext,
    settings: Settings,
    assembly_reports: AssemblyReportsService,
) -> None:
    if not is_admin(settings, _user_id(message)):
        await _deny_admin_access(message, settings)
        return
    if is_group_chat(message):
        return
    await _build_and_preview_assembly(message, bot, state, settings, assembly_reports)


@router.message(F.text == BTN_ASSEMBLY_REPORT, StateFilter(default_state))
async def start_assembly_report_button(
    message: Message,
    bot: Bot,
    state: FSMContext,
    settings: Settings,
    assembly_reports: AssemblyReportsService,
) -> None:
    if not is_admin(settings, _user_id(message)):
        await _deny_admin_access(message, settings)
        return
    if is_group_chat(message):
        return
    await _build_and_preview_assembly_report(message, bot, state, settings, assembly_reports)


@router.message(_AdminTagFilter("#планвыходов"))
async def handle_exit_plan_tag(
    message: Message,
    bot: Bot,
    state: FSMContext,
    settings: Settings,
    exit_reports: ExitReportsService,
) -> None:
    if not is_admin(settings, _user_id(message)):
        await _deny_admin_access(message, settings)
        return
    projects = parse_exit_plan_message(message.text or "")
    if not projects:
        if is_group_chat(message):
            await answer_text(message, MSG_EXIT_PLAN_GROUP_HINT, parse_mode=ParseMode.HTML)
        return
    await state.clear()
    await _build_and_preview_exit_plan(
        message, bot, state, settings, exit_reports, projects
    )


@router.message(_AdminTagFilter("#отчетвыходов"))
async def handle_exit_report_tag(
    message: Message,
    bot: Bot,
    state: FSMContext,
    settings: Settings,
    exit_reports: ExitReportsService,
) -> None:
    if not is_admin(settings, _user_id(message)):
        await _deny_admin_access(message, settings)
        return
    projects = parse_exit_report_message(message.text or "")
    if not projects:
        if is_group_chat(message):
            await answer_text(message, MSG_EXIT_REPORT_GROUP_HINT, parse_mode=ParseMode.HTML)
        return
    await state.clear()
    await _build_and_preview_exit_report(
        message, bot, state, settings, exit_reports, projects
    )


@router.message(_AdminTagFilter("#сборкарасходников"))
async def handle_assembly_tag(
    message: Message,
    bot: Bot,
    state: FSMContext,
    settings: Settings,
    assembly_reports: AssemblyReportsService,
) -> None:
    if not is_admin(settings, _user_id(message)):
        await _deny_admin_access(message, settings)
        return
    await state.clear()
    await _build_and_preview_assembly(message, bot, state, settings, assembly_reports)


@router.message(_AdminTagFilter("#отчетсборки"))
async def handle_assembly_report_tag(
    message: Message,
    bot: Bot,
    state: FSMContext,
    settings: Settings,
    assembly_reports: AssemblyReportsService,
) -> None:
    if not is_admin(settings, _user_id(message)):
        await _deny_admin_access(message, settings)
        return
    await state.clear()
    await _build_and_preview_assembly_report(message, bot, state, settings, assembly_reports)


async def _handle_project_selection(
    message: Message,
    bot: Bot,
    state: FSMContext,
    settings: Settings,
    exit_reports: ExitReportsService,
    *,
    flow: str,
) -> None:
    text = (message.text or "").strip()
    data = await state.get_data()
    available: list[str] = data.get("available_projects", [])
    selected: list[str] = list(data.get("selected_projects", []))

    if text in CANCEL_BUTTONS or text == BTN_MENU:
        await state.clear()
        label = MSG_MAIN_MENU if text == BTN_MENU else MSG_CANCELLED
        await answer_text(
            message,
            label,
            reply_markup=keyboard_for_message(settings, message),
            parse_mode=ParseMode.HTML,
        )
        return

    if text == BTN_DONE:
        if not selected:
            await answer_text(
                message,
                MSG_REGIONS_NEED_PROJECT,
                reply_markup=projects_keyboard(available),
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            resolved = await exit_reports.resolve_projects(selected)
        except ValueError as exc:
            await answer_text(
                message,
                str(exc),
                reply_markup=projects_keyboard(available),
                parse_mode=ParseMode.HTML,
            )
            return
        if flow == "exit_plan":
            await _build_and_preview_exit_plan(
                message, bot, state, settings, exit_reports, resolved
            )
        else:
            await _build_and_preview_exit_report(
                message, bot, state, settings, exit_reports, resolved
            )
        return

    additions = _parse_selection(text, available)
    if not additions:
        await answer_text(
            message,
            MSG_REGIONS_PICK_PROJECT,
            reply_markup=projects_keyboard(available),
            parse_mode=ParseMode.HTML,
        )
        return

    for project in additions:
        if project not in selected:
            selected.append(project)
    await state.update_data(selected_projects=selected)
    await answer_text(
        message,
        MSG_REGIONS_PROJECTS_UPDATED.format(selected=", ".join(selected)),
        reply_markup=projects_keyboard(available),
        parse_mode=ParseMode.HTML,
    )


@router.message(BotStates.exit_plan_projects, F.text)
async def handle_exit_plan_projects(
    message: Message,
    bot: Bot,
    state: FSMContext,
    settings: Settings,
    exit_reports: ExitReportsService,
) -> None:
    await _handle_project_selection(
        message,
        bot,
        state,
        settings,
        exit_reports,
        flow="exit_plan",
    )


@router.message(BotStates.exit_report_projects, F.text)
async def handle_exit_report_projects(
    message: Message,
    bot: Bot,
    state: FSMContext,
    settings: Settings,
    exit_reports: ExitReportsService,
) -> None:
    await _handle_project_selection(
        message,
        bot,
        state,
        settings,
        exit_reports,
        flow="exit_report",
    )


@router.message(BotStates.waiting_admin_report_confirm, F.text == BTN_DO_SEND)
async def confirm_admin_send(
    message: Message,
    bot: Bot,
    settings: Settings,
    state: FSMContext,
) -> None:
    user_id = _user_id(message)
    data = await state.get_data()
    if not is_admin(settings, user_id):
        await state.clear()
        await _deny_admin_access(message, settings)
        return
    if data.get("admin_report_user_id") != user_id:
        await state.clear()
        await answer_text(
            message,
            MSG_CANCELLED,
            reply_markup=keyboard_for_message(settings, message),
            parse_mode=ParseMode.HTML,
        )
        return

    text = data.get("admin_report_text", "")
    extra_text = data.get("admin_report_extra_text")
    await state.clear()
    try:
        await send_report_text(bot, settings, text)
        if extra_text:
            await send_report_text(bot, settings, extra_text)
    except Exception:
        logger.exception("Failed to send admin report after confirmation")
        await answer_text(
            message,
            MSG_ADMIN_SEND_ERROR,
            reply_markup=keyboard_for_message(settings, message),
            parse_mode=ParseMode.HTML,
        )
        return

    await answer_text(
        message,
        MSG_ADMIN_SENT,
        reply_markup=keyboard_for_message(settings, message),
        parse_mode=ParseMode.HTML,
    )


@router.message(BotStates.waiting_admin_report_confirm, F.text == BTN_BACK)
async def confirm_admin_back(message: Message, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    await answer_text(
        message,
        MSG_CANCELLED,
        reply_markup=keyboard_for_message(settings, message),
        parse_mode=ParseMode.HTML,
    )


@router.message(BotStates.waiting_admin_report_confirm, F.text)
async def confirm_admin_unknown(message: Message) -> None:
    await answer_text(
        message,
        MSG_DO_USE_BUTTONS,
        reply_markup=do_confirm_keyboard(),
        parse_mode=ParseMode.HTML,
    )


def _parse_selection(text: str, options: list[str]) -> list[str]:
    option_map = {option.casefold(): option for option in options}
    selected: list[str] = []
    parts = [text] if text in options else re.split(r"[,;]", text)
    for part in parts:
        key = part.strip().casefold()
        if not key:
            continue
        match = option_map.get(key)
        if match and match not in selected:
            selected.append(match)
    return selected
