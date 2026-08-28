from __future__ import annotations

import logging
import re

from aiogram import Bot, F, Router
from aiogram.enums import MessageEntityType
from aiogram.filters import Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.chat_utils import is_group_chat
from app.handlers.flows import answer_text
from app.handlers.region_transfer_flow import REGION_TRANSFER_HINT, reply_region_transfer
from app.keyboards import (
    BTN_CANCEL,
    BTN_DONE,
    BTN_MENU,
    BTN_REGIONS,
    CANCEL_BUTTONS,
    cancel_keyboard,
    main_keyboard,
    projects_keyboard,
)
from app.services.region_transfer import (
    RegionTransferRequest,
    RegionTransferService,
    parse_region_transfer_message,
)
from app.states import BotStates

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
        "Передать регионы\n\n"
        "Выберите проекты кнопками или отправьте через запятую.\n"
        "Когда закончите — нажмите «Готово».",
        reply_markup=projects_keyboard(projects),
    )


@router.message(RegionTransferTagFilter())
async def handle_region_transfer_tag(
    message: Message,
    bot: Bot,
    state: FSMContext,
    region_transfer: RegionTransferService,
) -> None:
    await state.clear()
    request = parse_region_transfer_message(message.text or "")
    if request:
        await reply_region_transfer(message, bot, region_transfer, request)
        return
    if is_group_chat(message):
        await answer_text(message, REGION_TRANSFER_HINT)
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
        await answer_text(message, REGION_TRANSFER_HINT)
        return
    await _start_private_flow(message, state, region_transfer)


@router.message(BotStates.regions_projects, F.text)
async def handle_regions_projects(
    message: Message,
    state: FSMContext,
    region_transfer: RegionTransferService,
) -> None:
    text = (message.text or "").strip()
    data = await state.get_data()
    available: list[str] = data.get("available_projects", [])
    selected: list[str] = list(data.get("selected_projects", []))

    if text in CANCEL_BUTTONS or text == BTN_MENU:
        await state.clear()
        label = "Главное меню." if text == BTN_MENU else "Отменено."
        await answer_text(message, label, reply_markup=main_keyboard())
        return

    if text == BTN_DONE:
        if not selected:
            await answer_text(
                message,
                "Сначала выберите хотя бы один проект.",
                reply_markup=projects_keyboard(available),
            )
            return
        try:
            resolved = await region_transfer.resolve_projects(selected)
        except ValueError as exc:
            await answer_text(message, str(exc), reply_markup=projects_keyboard(available))
            return
        managers = await region_transfer.list_managers(resolved)
        if not managers:
            await answer_text(
                message,
                "В выбранных проектах не найдено менеджеров.",
                reply_markup=projects_keyboard(available),
            )
            return
        await state.update_data(selected_projects=resolved)
        await state.set_state(BotStates.regions_manager)
        manager_list = "\n".join(f"• {name}" for name in managers)
        await answer_text(
            message,
            f"Выбрано проектов: {', '.join(resolved)}\n\n"
            f"Менеджеры:\n{manager_list}\n\n"
            "Введите имя менеджера точно как в списке.",
            reply_markup=cancel_keyboard(),
        )
        return

    additions = _parse_selection(text, available)
    if not additions:
        await answer_text(
            message,
            "Не понял выбор. Нажмите проект или отправьте названия через запятую.",
            reply_markup=projects_keyboard(available),
        )
        return

    for project in additions:
        if project not in selected:
            selected.append(project)
    await state.update_data(selected_projects=selected)
    await answer_text(
        message,
        f"Выбрано: {', '.join(selected)}\nНажмите «Готово», когда выберете все проекты.",
        reply_markup=projects_keyboard(available),
    )


@router.message(BotStates.regions_manager, F.text)
async def handle_regions_manager(
    message: Message,
    state: FSMContext,
    region_transfer: RegionTransferService,
) -> None:
    text = (message.text or "").strip()
    if text in CANCEL_BUTTONS or text == BTN_MENU:
        await state.clear()
        label = "Главное меню." if text == BTN_MENU else "Отменено."
        await answer_text(message, label, reply_markup=main_keyboard())
        return

    data = await state.get_data()
    projects: list[str] = data.get("selected_projects", [])
    managers = await region_transfer.list_managers(projects)
    manager = _pick_option(text, managers)
    if not manager:
        await answer_text(
            message,
            "Менеджер не найден. Введите имя точно как в списке.",
            reply_markup=cancel_keyboard(),
        )
        return

    regions = await region_transfer.list_regions(projects, manager)
    if not regions:
        await answer_text(
            message,
            f"У менеджера «{manager}» нет регионов в выбранных проектах.",
            reply_markup=cancel_keyboard(),
        )
        return

    await state.update_data(selected_manager=manager, available_regions=regions, selected_regions=[])
    await state.set_state(BotStates.regions_regions)
    region_list = "\n".join(f"• {name}" for name in regions)
    await answer_text(
        message,
        f"Менеджер: {manager}\n\n"
        f"Регионы:\n{region_list}\n\n"
        "Отправьте регионы через запятую или по одному.\n"
        "Когда закончите — нажмите «Готово».",
        reply_markup=projects_keyboard(regions),
    )


@router.message(BotStates.regions_regions, F.text)
async def handle_regions_regions(
    message: Message,
    bot: Bot,
    state: FSMContext,
    region_transfer: RegionTransferService,
) -> None:
    text = (message.text or "").strip()
    data = await state.get_data()
    available: list[str] = data.get("available_regions", [])
    selected: list[str] = list(data.get("selected_regions", []))
    projects: list[str] = data.get("selected_projects", [])
    manager: str = data.get("selected_manager", "")

    if text in CANCEL_BUTTONS or text == BTN_MENU:
        await state.clear()
        label = "Главное меню." if text == BTN_MENU else "Отменено."
        await answer_text(message, label, reply_markup=main_keyboard())
        return

    if text == BTN_DONE:
        if not selected:
            await answer_text(
                message,
                "Сначала выберите хотя бы один регион.",
                reply_markup=projects_keyboard(available),
            )
            return
        await state.clear()
        request = RegionTransferRequest(
            projects=tuple(projects),
            manager=manager,
            regions=tuple(selected),
        )
        await reply_region_transfer(message, bot, region_transfer, request)
        return

    additions = _parse_selection(text, available)
    if not additions:
        await answer_text(
            message,
            "Не понял выбор. Нажмите регион или отправьте названия через запятую.",
            reply_markup=projects_keyboard(available),
        )
        return

    for region in additions:
        if region not in selected:
            selected.append(region)
    await state.update_data(selected_regions=selected)
    await answer_text(
        message,
        f"Выбрано регионов: {len(selected)}\n{', '.join(selected)}\n\n"
        "Нажмите «Готово», когда выберете все регионы.",
        reply_markup=projects_keyboard(available),
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
