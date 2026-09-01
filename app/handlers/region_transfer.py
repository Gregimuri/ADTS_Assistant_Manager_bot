from __future__ import annotations

import logging
import re

from aiogram import Bot, F, Router
from aiogram.enums import MessageEntityType, ParseMode
from aiogram.filters import Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.chat_utils import is_group_chat
from app.access import keyboard_for_message
from app.config import Settings
from app.handlers.flows import answer_text
from app.handlers.region_transfer_flow import reply_region_transfer
from app.keyboards import (
    BTN_CANCEL,
    BTN_DONE,
    BTN_MENU,
    BTN_REGIONS,
    CANCEL_BUTTONS,
    cancel_keyboard,
    projects_keyboard,
)
from app.services.region_transfer import (
    RegionTransferRequest,
    RegionTransferService,
    parse_region_transfer_message,
)
from app.states import BotStates
from app.texts import (
    MSG_CANCELLED,
    MSG_MAIN_MENU,
    MSG_REGIONS_MANAGER_NOT_FOUND,
    MSG_REGIONS_NEED_PROJECT,
    MSG_REGIONS_NEED_REGION,
    MSG_REGIONS_NO_MANAGERS,
    MSG_REGIONS_NO_REGIONS,
    MSG_REGIONS_PICK_PROJECT,
    MSG_REGIONS_PICK_REGION,
    MSG_REGIONS_PICK_REGIONS,
    MSG_REGIONS_PROJECTS_SELECTED,
    MSG_REGIONS_PROJECTS_UPDATED,
    MSG_REGIONS_REGIONS_UPDATED,
    PROMPT_REGIONS_START,
    REGION_TRANSFER_HINT,
)

logger = logging.getLogger(__name__)

router = Router(name="region_transfer")

_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)
_TAG_LINE_RE = re.compile(r"^#передатьрегионы(?:\s|$)", re.IGNORECASE)


def _normalize_tag_text(text: str) -> str:
    return text.translate(_INVISIBLE).replace("\xa0", " ").strip()


class RegionTransferTagFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        text = message.text or message.caption or ""
        if not text:
            return False
        normalized = _normalize_tag_text(text)
        if not normalized:
            return False
        first_line = normalized.splitlines()[0].strip()
        if first_line.casefold().startswith("#передатьрегионы"):
            return True
        source = message.text or message.caption or ""
        for entity in message.entities or message.caption_entities or ():
            if entity.type != MessageEntityType.HASHTAG:
                continue
            frag = _normalize_tag_text(source[entity.offset : entity.offset + entity.length])
            if frag.casefold().startswith("#передатьрегионы"):
                return True
        return False


async def _start_private_flow(
    message: Message,
    state: FSMContext,
    service: RegionTransferService,
) -> None:
    projects = await service.list_transfer_projects()
    await state.set_state(BotStates.regions_projects)
    await state.update_data(
        selected_projects=[],
        available_projects=projects,
        selected_regions=[],
    )
    await answer_text(
        message,
        PROMPT_REGIONS_START,
        reply_markup=projects_keyboard(projects),
        parse_mode=ParseMode.HTML,
    )


@router.message(RegionTransferTagFilter())
async def handle_region_transfer_tag(
    message: Message,
    bot: Bot,
    state: FSMContext,
    region_transfer: RegionTransferService,
    settings: Settings,
) -> None:
    await state.clear()
    request = parse_region_transfer_message(message.text or "")
    if request:
        await reply_region_transfer(message, bot, region_transfer, settings, request)
        return
    if is_group_chat(message):
        await answer_text(message, REGION_TRANSFER_HINT, parse_mode=ParseMode.HTML)
        return
    await _start_private_flow(message, state, region_transfer)


@router.message(F.text == BTN_REGIONS)
async def start_region_transfer_button(
    message: Message,
    state: FSMContext,
    region_transfer: RegionTransferService,
) -> None:
    if is_group_chat(message):
        await state.clear()
        await answer_text(message, REGION_TRANSFER_HINT, parse_mode=ParseMode.HTML)
        return
    await _start_private_flow(message, state, region_transfer)


@router.message(BotStates.regions_projects, F.text)
async def handle_regions_projects(
    message: Message,
    state: FSMContext,
    region_transfer: RegionTransferService,
    settings: Settings,
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
            resolved = await region_transfer.resolve_projects(selected)
        except ValueError as exc:
            await answer_text(
                message,
                str(exc),
                reply_markup=projects_keyboard(available),
                parse_mode=ParseMode.HTML,
            )
            return
        managers = await region_transfer.list_managers(resolved)
        if not managers:
            await answer_text(
                message,
                MSG_REGIONS_NO_MANAGERS,
                reply_markup=projects_keyboard(available),
                parse_mode=ParseMode.HTML,
            )
            return
        await state.update_data(selected_projects=resolved)
        await state.set_state(BotStates.regions_manager)
        manager_list = "\n".join(f"• {name}" for name in managers)
        await answer_text(
            message,
            MSG_REGIONS_PROJECTS_SELECTED.format(
                projects=", ".join(resolved),
                manager_list=manager_list,
            ),
            reply_markup=cancel_keyboard(),
            parse_mode=ParseMode.HTML,
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


@router.message(BotStates.regions_manager, F.text)
async def handle_regions_manager(
    message: Message,
    state: FSMContext,
    region_transfer: RegionTransferService,
    settings: Settings,
) -> None:
    text = (message.text or "").strip()
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

    data = await state.get_data()
    projects: list[str] = data.get("selected_projects", [])
    managers = await region_transfer.list_managers(projects)
    manager = _pick_option(text, managers)
    if not manager:
        await answer_text(
            message,
            MSG_REGIONS_MANAGER_NOT_FOUND,
            reply_markup=cancel_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    regions = await region_transfer.list_regions(projects, manager)
    if not regions:
        await answer_text(
            message,
            MSG_REGIONS_NO_REGIONS.format(manager=manager),
            reply_markup=cancel_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    await state.update_data(selected_manager=manager, available_regions=regions, selected_regions=[])
    await state.set_state(BotStates.regions_regions)
    region_list = "\n".join(f"• {name}" for name in regions)
    await answer_text(
        message,
        MSG_REGIONS_PICK_REGIONS.format(manager=manager, region_list=region_list),
        reply_markup=projects_keyboard(regions),
        parse_mode=ParseMode.HTML,
    )


@router.message(BotStates.regions_regions, F.text)
async def handle_regions_regions(
    message: Message,
    bot: Bot,
    state: FSMContext,
    region_transfer: RegionTransferService,
    settings: Settings,
) -> None:
    text = (message.text or "").strip()
    data = await state.get_data()
    available: list[str] = data.get("available_regions", [])
    selected: list[str] = list(data.get("selected_regions", []))
    projects: list[str] = data.get("selected_projects", [])
    manager: str = data.get("selected_manager", "")

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
                MSG_REGIONS_NEED_REGION,
                reply_markup=projects_keyboard(available),
                parse_mode=ParseMode.HTML,
            )
            return
        await state.clear()
        request = RegionTransferRequest(
            projects=tuple(projects),
            manager=manager,
            regions=tuple(selected),
        )
        await reply_region_transfer(message, bot, region_transfer, settings, request)
        return

    additions = _parse_selection(text, available)
    if not additions:
        await answer_text(
            message,
            MSG_REGIONS_PICK_REGION,
            reply_markup=projects_keyboard(available),
            parse_mode=ParseMode.HTML,
        )
        return

    for region in additions:
        if region not in selected:
            selected.append(region)
    await state.update_data(selected_regions=selected)
    await answer_text(
        message,
        MSG_REGIONS_REGIONS_UPDATED.format(
            count=len(selected),
            selected=", ".join(selected),
        ),
        reply_markup=projects_keyboard(available),
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


def _pick_option(text: str, options: list[str]) -> str | None:
    normalized = text.strip().casefold()
    for option in options:
        if option.casefold() == normalized:
            return option
    return None
