from __future__ import annotations

import html
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction, MessageEntityType, ParseMode
from aiogram.filters import Filter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import Message

from app.access import keyboard_for_message
from app.chat_utils import is_group_chat
from app.config import Settings
from app.handlers.flows import answer_text
from app.keyboards import (
    BTN_FO,
    BTN_FO_BUILD,
    BTN_FO_BUILD_URGENT,
    BTN_FO_TECHMONT,
    BTN_FO_TECHMONT_TEST,
    BTN_MENU,
    BTN_TECHMONT,
    CANCEL_BUTTONS,
    FLOW_BUTTONS,
    cancel_keyboard,
    fo_mode_keyboard,
    fo_photos_keyboard,
    fo_points_keyboard,
    fo_projects_keyboard,
)
from app.services.adts_platform import AdtsPlatformClient, AdtsPlatformError, PlatformFoSubmit
from app.services.final_report import (
    FO_PROJECTS,
    FinalReportError,
    FinalReportPhoto,
    FinalReportService,
)
from app.services.sheets import ProjectStore, SheetsError
from app.telegram_files import download_telegram_file
from app.states import BotStates
from app.texts import (
    MSG_CANCELLED,
    MSG_FO_BAD_FILE,
    MSG_FO_BUILDING,
    MSG_FO_BUILDING_URGENT,
    MSG_FO_DONE,
    MSG_FO_ERROR,
    MSG_FO_GROUP_HINT,
    MSG_FO_NEED_PHOTO,
    MSG_FO_PHOTO_ADDED,
    MSG_FO_PICK_PROJECT,
    MSG_FO_PICK_MODE,
    MSG_FO_PROMPT_MODE,
    MSG_FO_PROMPT_PHOTOS,
    MSG_FO_PROMPT_PROJECT,
    MSG_FO_PROMPT_TT,
    MSG_FO_SHEET_ERROR,
    MSG_FO_TECHMONT_BUILDING,
    MSG_FO_TECHMONT_TEST_BUILDING,
    MSG_FO_TECHMONT_DONE,
    MSG_FO_TECHMONT_NO_HOOK,
    MSG_FO_TECHMONT_NO_POINTS,
    MSG_FO_TECHMONT_PROMPT_TT,
    MSG_MAIN_MENU,
)

FO_KIND_TECHMONT_TEST = "techmont_test"
FO_KIND_TECHMONT = "techmont"
FO_KIND_BITRIX = "bitrix"
_TECHMONT_KINDS = {FO_KIND_TECHMONT_TEST, FO_KIND_TECHMONT}
_TECHMONT_TITLES = {
    FO_KIND_TECHMONT_TEST: BTN_FO_TECHMONT_TEST,
    FO_KIND_TECHMONT: BTN_FO_TECHMONT,
}

logger = logging.getLogger(__name__)

router = Router(name="final_report")

_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)

_VIDEO_EXT = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    "video/mpeg": ".mpeg",
}


def _fo_mime_allowed(mime: str) -> bool:
    lowered = (mime or "").casefold()
    return lowered.startswith("image/") or lowered.startswith("video/")


def _video_filename(
    *,
    index: int,
    file_name: str | None,
    mime_type: str | None,
) -> str:
    if file_name and file_name.strip():
        return file_name.strip()
    ext = _VIDEO_EXT.get((mime_type or "").casefold(), ".mp4")
    return f"video_{index}{ext}"


async def _append_fo_media(
    state: FSMContext,
    *,
    file_id: str,
    file_unique_id: str,
    filename: str,
    file_size: int | None = None,
) -> int:
    data = await state.get_data()
    photos = list(data.get("fo_photos") or [])
    unique_ids = {item.get("file_unique_id") for item in photos}
    if file_unique_id not in unique_ids:
        item: dict[str, object] = {
            "file_id": file_id,
            "file_unique_id": file_unique_id,
            "filename": filename,
        }
        if file_size is not None:
            item["file_size"] = file_size
        photos.append(item)
        await state.update_data(fo_photos=photos)
    return len(photos)


def _normalize_tag_text(text: str) -> str:
    return text.translate(_INVISIBLE).replace("\xa0", " ").strip()


class FinalReportTagFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        text = message.text or message.caption or ""
        if not text:
            return False
        normalized = _normalize_tag_text(text)
        if not normalized:
            return False
        first_line = normalized.splitlines()[0].strip()
        if first_line.casefold().startswith("#сдатьфо"):
            return True
        source = message.text or message.caption or ""
        for entity in message.entities or message.caption_entities or ():
            if entity.type != MessageEntityType.HASHTAG:
                continue
            frag = _normalize_tag_text(source[entity.offset : entity.offset + entity.length])
            if frag.casefold().startswith("#сдатьфо"):
                return True
        return False


async def _start_flow(
    message: Message,
    state: FSMContext,
    service: FinalReportService,
) -> None:
    """Старый ФО: сразу выбор проекта ММ/ДО/Лента/МА/ШБ."""
    projects = service.supported_projects()
    await state.set_state(BotStates.fo_project)
    await state.update_data(fo_photos=[], fo_kind=FO_KIND_BITRIX)
    await answer_text(
        message,
        MSG_FO_PROMPT_PROJECT,
        reply_markup=fo_projects_keyboard(projects),
        parse_mode=ParseMode.HTML,
    )


async def _start_techmont_menu(message: Message, state: FSMContext) -> None:
    await state.set_state(BotStates.fo_mode)
    await state.update_data(fo_photos=[], fo_kind="")
    await answer_text(
        message,
        MSG_FO_PROMPT_MODE,
        reply_markup=fo_mode_keyboard(),
        parse_mode=ParseMode.HTML,
    )


def _platform_client(settings: Settings, kind: str) -> AdtsPlatformClient:
    if kind == FO_KIND_TECHMONT:
        hook = (settings.adts_techmont_hook_url or settings.adts_hook_url).strip()
        return AdtsPlatformClient(settings, hook_url=hook)
    return AdtsPlatformClient(settings)


def _points_suffix(names: list[str]) -> str:
    if not names:
        return ""
    sample = ", ".join(html.escape(name) for name in names[:8])
    extra = "" if len(names) <= 8 else f" и ещё {len(names) - 8}"
    return f". Сейчас: {sample}{extra}"


@router.message(FinalReportTagFilter())
async def handle_fo_tag(
    message: Message,
    state: FSMContext,
    final_report: FinalReportService,
) -> None:
    if is_group_chat(message):
        await state.clear()
        await answer_text(message, MSG_FO_GROUP_HINT, parse_mode=ParseMode.HTML)
        return
    await _start_flow(message, state, final_report)


@router.message(F.text == BTN_FO, StateFilter(default_state))
async def start_fo_button(
    message: Message,
    state: FSMContext,
    final_report: FinalReportService,
) -> None:
    if is_group_chat(message):
        await state.clear()
        await answer_text(message, MSG_FO_GROUP_HINT, parse_mode=ParseMode.HTML)
        return
    await _start_flow(message, state, final_report)


@router.message(F.text == BTN_TECHMONT, StateFilter(default_state))
async def start_techmont_button(
    message: Message,
    state: FSMContext,
) -> None:
    if is_group_chat(message):
        await state.clear()
        await answer_text(message, MSG_FO_GROUP_HINT, parse_mode=ParseMode.HTML)
        return
    await _start_techmont_menu(message, state)


@router.message(BotStates.fo_mode, F.text)
async def handle_fo_mode(
    message: Message,
    state: FSMContext,
    final_report: FinalReportService,
    settings: Settings,
) -> None:
    del final_report
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
    kind = ""
    if text == BTN_FO_TECHMONT_TEST:
        kind = FO_KIND_TECHMONT_TEST
    elif text == BTN_FO_TECHMONT:
        kind = FO_KIND_TECHMONT
    if kind:
        title = _TECHMONT_TITLES[kind]
        client = _platform_client(settings, kind)
        if not client.enabled():
            await answer_text(
                message,
                MSG_FO_TECHMONT_NO_HOOK.format(title=html.escape(title)),
                reply_markup=fo_mode_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            points = await client.list_points()
        except AdtsPlatformError as exc:
            await answer_text(
                message,
                MSG_FO_ERROR.format(error=html.escape(str(exc))),
                reply_markup=fo_mode_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            return
        if not points:
            await answer_text(
                message,
                MSG_FO_TECHMONT_NO_POINTS,
                reply_markup=fo_mode_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            return
        names = [item.name for item in points]
        await state.update_data(fo_kind=kind, fo_photos=[], fo_project=title)
        await state.set_state(BotStates.fo_tt)
        await answer_text(
            message,
            MSG_FO_TECHMONT_PROMPT_TT.format(title=html.escape(title), points=_points_suffix(names)),
            reply_markup=fo_points_keyboard(names[:12]),
            parse_mode=ParseMode.HTML,
        )
        return
    await answer_text(
        message,
        MSG_FO_PICK_MODE,
        reply_markup=fo_mode_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@router.message(BotStates.fo_project, F.text)
async def handle_fo_project(
    message: Message,
    state: FSMContext,
    final_report: FinalReportService,
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
    try:
        project = final_report.resolve_project(text)
    except ValueError:
        await answer_text(
            message,
            MSG_FO_PICK_PROJECT,
            reply_markup=fo_projects_keyboard(list(FO_PROJECTS)),
            parse_mode=ParseMode.HTML,
        )
        return
    await state.update_data(fo_project=project, fo_photos=[])
    await state.set_state(BotStates.fo_tt)
    await answer_text(
        message,
        MSG_FO_PROMPT_TT.format(project=project),
        reply_markup=cancel_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@router.message(BotStates.fo_tt, F.text)
async def handle_fo_tt(
    message: Message,
    state: FSMContext,
    final_report: FinalReportService,
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
    if text in FLOW_BUTTONS:
        return
    data = await state.get_data()
    project = str(data.get("fo_project") or "")
    kind = str(data.get("fo_kind") or FO_KIND_BITRIX)
    if kind in _TECHMONT_KINDS:
        try:
            point = await _platform_client(settings, kind).find_point(text)
        except AdtsPlatformError as exc:
            await answer_text(
                message,
                str(exc),
                reply_markup=cancel_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            return
        await state.update_data(
            fo_store_name=point.name,
            fo_store_manager=point.manager,
            fo_store_region=point.region,
            fo_store_address=point.address,
            fo_photos=[],
        )
        await state.set_state(BotStates.fo_photos)
        await answer_text(
            message,
            MSG_FO_PROMPT_PHOTOS.format(
                project=_TECHMONT_TITLES.get(kind, "техмонт"),
                store=point.name,
            ),
            reply_markup=fo_photos_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        store = await final_report.find_store(project, text)
    except ValueError as exc:
        await answer_text(
            message,
            str(exc),
            reply_markup=cancel_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    except SheetsError:
        logger.exception("Failed to load project sheet for FO")
        await answer_text(
            message,
            MSG_FO_SHEET_ERROR,
            reply_markup=cancel_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    await state.update_data(
        fo_store_name=store.name,
        fo_store_manager=store.manager,
        fo_store_region=store.region,
        fo_store_address=store.address,
        fo_photos=[],
    )
    await state.set_state(BotStates.fo_photos)
    await answer_text(
        message,
        MSG_FO_PROMPT_PHOTOS.format(project=project, store=store.name),
        reply_markup=fo_photos_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@router.message(BotStates.fo_photos, F.photo)
async def handle_fo_photo(
    message: Message,
    state: FSMContext,
) -> None:
    photo = message.photo[-1]
    data = await state.get_data()
    index = len(data.get("fo_photos") or []) + 1
    count = await _append_fo_media(
        state,
        file_id=photo.file_id,
        file_unique_id=photo.file_unique_id,
        filename=f"photo_{index}.jpg",
        file_size=photo.file_size,
    )
    await answer_text(
        message,
        MSG_FO_PHOTO_ADDED.format(count=count),
        reply_markup=fo_photos_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@router.message(BotStates.fo_photos, F.video)
async def handle_fo_video(
    message: Message,
    state: FSMContext,
) -> None:
    video = message.video
    if video is None:
        return
    data = await state.get_data()
    index = len(data.get("fo_photos") or []) + 1
    count = await _append_fo_media(
        state,
        file_id=video.file_id,
        file_unique_id=video.file_unique_id,
        filename=_video_filename(
            index=index,
            file_name=video.file_name,
            mime_type=video.mime_type,
        ),
        file_size=video.file_size,
    )
    await answer_text(
        message,
        MSG_FO_PHOTO_ADDED.format(count=count),
        reply_markup=fo_photos_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@router.message(BotStates.fo_photos, F.video_note)
async def handle_fo_video_note(
    message: Message,
    state: FSMContext,
) -> None:
    note = message.video_note
    if note is None:
        return
    data = await state.get_data()
    index = len(data.get("fo_photos") or []) + 1
    count = await _append_fo_media(
        state,
        file_id=note.file_id,
        file_unique_id=note.file_unique_id,
        filename=f"video_note_{index}.mp4",
        file_size=note.file_size,
    )
    await answer_text(
        message,
        MSG_FO_PHOTO_ADDED.format(count=count),
        reply_markup=fo_photos_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@router.message(BotStates.fo_photos, F.document)
async def handle_fo_document_image(
    message: Message,
    state: FSMContext,
) -> None:
    document = message.document
    if document is None:
        return
    mime = (document.mime_type or "").casefold()
    if not _fo_mime_allowed(mime):
        await answer_text(
            message,
            MSG_FO_BAD_FILE,
            reply_markup=fo_photos_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    data = await state.get_data()
    index = len(data.get("fo_photos") or []) + 1
    if mime.startswith("video/"):
        filename = _video_filename(
            index=index,
            file_name=document.file_name,
            mime_type=document.mime_type,
        )
    else:
        ext = ".jpg"
        if document.file_name and "." in document.file_name:
            ext = "." + document.file_name.rsplit(".", 1)[-1]
        elif "png" in mime:
            ext = ".png"
        elif "webp" in mime:
            ext = ".webp"
        filename = document.file_name or f"photo_{index}{ext}"
    count = await _append_fo_media(
        state,
        file_id=document.file_id,
        file_unique_id=document.file_unique_id,
        filename=filename,
        file_size=document.file_size,
    )
    await answer_text(
        message,
        MSG_FO_PHOTO_ADDED.format(count=count),
        reply_markup=fo_photos_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@router.message(BotStates.fo_photos, F.text.in_({BTN_FO_BUILD, BTN_FO_BUILD_URGENT}))
async def handle_fo_build(
    message: Message,
    bot: Bot,
    state: FSMContext,
    final_report: FinalReportService,
    settings: Settings,
) -> None:
    data = await state.get_data()
    raw_photos = list(data.get("fo_photos") or [])
    if not raw_photos:
        await answer_text(
            message,
            MSG_FO_NEED_PHOTO,
            reply_markup=fo_photos_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    urgent = (message.text or "").strip() == BTN_FO_BUILD_URGENT
    kind = str(data.get("fo_kind") or FO_KIND_BITRIX)
    project = str(data.get("fo_project") or "")
    store_name = str(data.get("fo_store_name") or "")
    store = ProjectStore(
        project=project,
        name=store_name,
        region=str(data.get("fo_store_region") or ""),
        address=str(data.get("fo_store_address") or ""),
        manager=str(data.get("fo_store_manager") or ""),
    )
    photos = [
        FinalReportPhoto(
            file_id=str(item["file_id"]),
            file_unique_id=str(item["file_unique_id"]),
            filename=str(item.get("filename") or f"photo_{index}.jpg"),
        )
        for index, item in enumerate(raw_photos, start=1)
    ]

    if kind == FO_KIND_TECHMONT_TEST:
        building = MSG_FO_TECHMONT_TEST_BUILDING
    elif kind in _TECHMONT_KINDS:
        building = MSG_FO_TECHMONT_BUILDING
    elif urgent:
        building = MSG_FO_BUILDING_URGENT
    else:
        building = MSG_FO_BUILDING
    await answer_text(
        message,
        building,
        reply_markup=fo_photos_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    await bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_DOCUMENT)

    file_sizes: dict[str, int | None] = {}
    for item in raw_photos:
        fid = str(item.get("file_id") or "")
        size = item.get("file_size")
        file_sizes[fid] = int(size) if size is not None else None

    async def download_photo(file_id: str) -> bytes:
        return await download_telegram_file(
            bot,
            file_id,
            settings,
            file_size=file_sizes.get(file_id),
        )

    try:
        if kind in _TECHMONT_KINDS:
            uploaded: list[tuple[str, bytes]] = []
            for index, photo in enumerate(photos, start=1):
                content = await download_photo(photo.file_id)
                if content:
                    uploaded.append((photo.filename or f"photo_{index}.jpg", content))
            folder_url = ""
            disk_file_ids: list[str] = []
            if kind == FO_KIND_TECHMONT_TEST:
                folder_id, folder_url, _folder_name = await final_report.create_test_techmont_folder(
                    store_name
                )
                disk_file_ids = await final_report.upload_fo_files(folder_id, uploaded)
            elif kind == FO_KIND_TECHMONT:
                folder_id, folder_url, _folder_name = await final_report.create_techmont_mm_folder(
                    store_name
                )
                disk_file_ids = await final_report.upload_fo_files(folder_id, uploaded)
            result_text = "Срочный фотоотчёт" if urgent else "Фотоотчёт"
            client = _platform_client(settings, kind)
            # 1 диск → 2 ссылка в колонку → 3 результат платформы → 4 результат Bitrix
            # Ссылка до result: иначе spawn «ФО Bitrix» уходит с пустым $Ссылка на ФО$.
            column_id = await client.write_fo_link_for_point(
                point_name=store_name,
                folder_url=folder_url,
            )
            platform = await client.submit_result_for_point(
                point_name=store_name,
                files=uploaded,
                result_text=result_text,
                folder_url=folder_url,
                write_link=False,
            )
            if platform.bitrix_task_id and disk_file_ids:
                try:
                    await final_report.attach_fo_result_to_bitrix_task(
                        bitrix_task_id=platform.bitrix_task_id,
                        file_ids=disk_file_ids,
                        result_text=result_text,
                        folder_url=folder_url,
                    )
                except FinalReportError as exc:
                    logger.warning(
                        "Bitrix result attach failed for task %s: %s",
                        platform.bitrix_task_id,
                        exc,
                    )
            platform = PlatformFoSubmit(
                point_name=platform.point_name,
                column_id=column_id,
                folder_url=platform.folder_url or folder_url,
                photos=platform.photos,
                platform_url=platform.platform_url,
                bitrix_task_id=platform.bitrix_task_id,
            )
            await state.clear()
            await answer_text(
                message,
                MSG_FO_TECHMONT_DONE.format(
                    title=html.escape(_TECHMONT_TITLES.get(kind, "Техмонт")),
                    store=html.escape(platform.point_name),
                    photos=platform.photos,
                    column=html.escape(platform.column_id),
                    folder_url=html.escape(platform.folder_url),
                    platform_url=html.escape(platform.platform_url),
                ),
                reply_markup=keyboard_for_message(settings, message),
                parse_mode=ParseMode.HTML,
            )
            return

        result = await final_report.submit(
            project=project,
            store=store,
            photos=photos,
            download_photo=download_photo,
            urgent=urgent,
        )
    except (FinalReportError, AdtsPlatformError) as exc:
        logger.exception("FO submit failed")
        await answer_text(
            message,
            MSG_FO_ERROR.format(error=html.escape(str(exc))),
            reply_markup=fo_photos_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception as exc:
        logger.exception("Unexpected FO submit failure")
        await answer_text(
            message,
            MSG_FO_ERROR.format(error=html.escape(f"{type(exc).__name__}: {exc}")),
            reply_markup=fo_photos_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    await state.clear()
    await answer_text(
        message,
        MSG_FO_DONE.format(
            project=html.escape(result.project),
            store=html.escape(result.store_name),
            folder=html.escape(result.folder_name),
            photos=result.photos_uploaded,
            creator=html.escape(result.creator_name),
            folder_url=html.escape(result.folder_url),
            task_url=html.escape(result.task_url),
        ),
        reply_markup=keyboard_for_message(settings, message),
        parse_mode=ParseMode.HTML,
    )


@router.message(BotStates.fo_photos, F.text)
async def handle_fo_photos_text(
    message: Message,
    state: FSMContext,
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
    await answer_text(
        message,
        "Пришлите фото или видео и нажмите «Сформировать отчет», либо «Отмена».",
        reply_markup=fo_photos_keyboard(),
        parse_mode=ParseMode.HTML,
    )
