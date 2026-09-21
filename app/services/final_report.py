from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
import aiohttp

from app.config import Settings
from app.services.bitrix_rest import bitrix_call
from app.services.catalog import Catalog
from app.services.fo_managers import FoManagerRegistry
from app.services.report_storage import ReportStorage
from app.services.sheets import ProjectStore

logger = logging.getLogger(__name__)

_MSK = timezone(timedelta(hours=3))

# Проект таблицы → Bitrix-группа (поле «Проект» в задаче)
FO_PROJECT_GROUPS: dict[str, int] = {
    "ММ": 25,
    "ДО": 25,
    "Лента": 133,
    "МА": 151,
    "ШБ": 197,
}

# Родительские папки на диске (ссылки /folder/...)
FO_PROJECT_PARENT_FOLDERS: dict[str, int] = {
    "ММ": 331645,  # https://adts.bitrix24.ru/folder/SBAuo8TRAs5PB0NjAFS8
    "ДО": 331645,
    "МА": 459001,  # https://adts.bitrix24.ru/folder/6aMkgrXAiAeTfpzlLJOj
    "Лента": 559455,  # https://adts.bitrix24.ru/folder/RWAXhqRIow0NvpXY0QKa
    "ШБ": 910859,  # https://adts.bitrix24.ru/folder/opcbi4a5u8e2fr883ulZ
}

FO_PROJECTS = tuple(FO_PROJECT_GROUPS.keys())

_FOLDER_LINK = (
    "https://adts.bitrix24.ru/bitrix/tools/disk/focus.php"
    "?folderId={folder_id}&action=openFolderList&ncc=1"
)


class FinalReportError(RuntimeError):
    """Ошибка сдачи финального отчёта."""


@dataclass(frozen=True, slots=True)
class FinalReportPhoto:
    file_id: str
    file_unique_id: str
    filename: str


@dataclass(frozen=True, slots=True)
class FinalReportResult:
    task_id: str
    task_url: str
    folder_id: str
    folder_url: str
    folder_name: str
    photos_uploaded: int
    creator_name: str
    store_name: str
    project: str


class FinalReportService:
    def __init__(
        self,
        settings: Settings,
        catalog: Catalog,
        managers: FoManagerRegistry,
        storage: ReportStorage | None = None,
    ) -> None:
        self._settings = settings
        self._catalog = catalog
        self._managers = managers
        self._storage = storage

    def supported_projects(self) -> list[str]:
        return list(FO_PROJECTS)

    def resolve_project(self, raw: str) -> str:
        key = raw.strip().casefold()
        for name in FO_PROJECTS:
            if name.casefold() == key:
                return name
        raise ValueError(f"Проект «{raw.strip()}» не поддерживается для сдачи ФО.")

    async def find_store(self, project: str, query: str) -> ProjectStore:
        project = self.resolve_project(project)
        matched = await self._catalog.find_project_stores(project, query.strip())
        if not matched:
            raise ValueError(
                f"ТТ «{query.strip()}» не найдена в проекте {project}. "
                "Проверьте название и введите ещё раз."
            )
        unique_names = {store.name for store in matched}
        if len(unique_names) > 1:
            sample = ", ".join(sorted(unique_names)[:5])
            raise ValueError(
                "Найдено несколько ТТ. Уточните название.\n"
                f"Подходят: {sample}"
            )
        return matched[0]

    async def submit(
        self,
        *,
        project: str,
        store: ProjectStore,
        photos: list[FinalReportPhoto],
        download_photo,
    ) -> FinalReportResult:
        if not photos:
            raise FinalReportError("Нужно хотя бы одно фото или видео.")
        if not self._settings.bitrix_webhook_url.strip():
            raise FinalReportError("BITRIX_WEBHOOK_URL не задан.")

        project = self.resolve_project(project)
        group_id = FO_PROJECT_GROUPS[project]
        upload_folder_name = _fo_upload_folder_name(project, store.name)
        folder_id = await self._resolve_upload_folder_id(project, store.name)
        if not folder_id:
            raise FinalReportError("Bitrix не вернул ID папки для загрузки ФО.")

        uploaded = 0
        for index, photo in enumerate(photos, start=1):
            try:
                content = await download_photo(photo.file_id)
            except FinalReportError:
                raise
            except Exception as exc:
                raise FinalReportError(
                    f"Не удалось скачать файл №{index} из Telegram: {exc}"
                ) from exc
            if not content:
                continue
            filename = photo.filename or f"photo_{index}.jpg"
            try:
                await self._upload_file(folder_id, filename, content)
            except FinalReportError:
                raise
            except Exception as exc:
                raise FinalReportError(
                    f"Не удалось загрузить файл №{index} на диск Bitrix: {exc}"
                ) from exc
            uploaded += 1
        if uploaded == 0:
            raise FinalReportError("Не удалось скачать файлы из Telegram.")

        folder_url = _FOLDER_LINK.format(folder_id=folder_id)
        manager_id, manager_name = await self._managers.resolve(store.manager)
        api_user_id = self._settings.bitrix_fo_fallback_creator_id  # webhook = Титков
        # Постановщик в карточке — менеджер ТТ (выставляется через update после создания).
        created_by_id = manager_id if manager_id > 0 else api_user_id
        deadline = _deadline_for_fo()
        description = (
            f"Постановщик (менеджер ТТ): {manager_name}\n"
            f"Ссылка на диск: {folder_url}"
        )
        responsible_id = await self._next_responsible_id()
        auditor_ids = sorted(
            set(self._settings.bitrix_fo_auditor_ids)
            | set(self._settings.bitrix_fo_responsible_ids)
            | {responsible_id, api_user_id}
        )
        crm_items = await self._find_crm_task_bindings(project, store.name)
        title = f'Финальный отчет - "{project} {store.name}"'
        try:
            task = await self._create_task(
                title=title,
                description=description,
                responsible_id=responsible_id,
                created_by_id=created_by_id,
                auditor_ids=auditor_ids,
                group_id=group_id,
                deadline=deadline,
                crm_items=crm_items,
            )
        except FinalReportError as exc:
            if not crm_items:
                raise
            logger.warning(
                "FO task create with CRM %s failed (%s), retry without CRM",
                crm_items,
                exc,
            )
            task = await self._create_task(
                title=title,
                description=description,
                responsible_id=responsible_id,
                created_by_id=created_by_id,
                auditor_ids=auditor_ids,
                group_id=group_id,
                deadline=deadline,
                crm_items=None,
            )
        task_id = str(task.get("id") or task.get("ID") or "")
        if not task_id:
            raise FinalReportError("Задача создана, но Bitrix не вернул ID.")
        task_url = self._settings.bitrix_task_url_template.format(task_id=task_id)
        return FinalReportResult(
            task_id=task_id,
            task_url=task_url,
            folder_id=folder_id,
            folder_url=folder_url,
            folder_name=upload_folder_name,
            photos_uploaded=uploaded,
            creator_name=manager_name,
            store_name=store.name,
            project=project,
        )

    async def _next_responsible_id(self) -> int:
        candidates = self._settings.bitrix_fo_responsible_ids
        if not candidates:
            raise FinalReportError("Не задан список исполнителей ФО.")
        if self._storage is None:
            return int(candidates[0])
        try:
            return await self._storage.next_fo_responsible_id(candidates)
        except ValueError as exc:
            raise FinalReportError(str(exc)) from exc

    async def _resolve_parent_folder(self, project: str) -> str:
        """Папка проекта на диске Bitrix, куда складываются ФО."""
        parent_id = FO_PROJECT_PARENT_FOLDERS.get(project)
        if not parent_id:
            raise FinalReportError(f"Для проекта {project} не задана папка на диске.")
        try:
            result = await self._call("disk.folder.get", {"id": parent_id})
        except FinalReportError as exc:
            text = str(exc).casefold()
            if "insufficient_scope" in text:
                raise FinalReportError(
                    "У webhook Bitrix нет права Disk. "
                    "Добавьте доступ «Диск» (disk) во входящий webhook и повторите."
                ) from exc
            raise
        if not isinstance(result, dict):
            raise FinalReportError(
                f"Не найдена папка проекта {project} на диске Bitrix (id={parent_id})."
            )
        # Ссылки /folder/... могут указывать на ярлык — пишем в реальный объект.
        folder_id = str(
            result.get("REAL_OBJECT_ID")
            or result.get("realObjectId")
            or result.get("ID")
            or result.get("id")
            or parent_id
        )
        return folder_id

    async def _resolve_upload_folder_id(self, project: str, store_name: str) -> str:
        """Внутри папки ТТ всегда создаётся/находится «Итоговый фотоотчет …» для загрузки."""
        project_root = await self._resolve_parent_folder(project)
        tt_name = store_name.strip()
        fo_name = _fo_upload_folder_name(project, tt_name)

        tt_folder = await self._locate_tt_folder(project_root, project, tt_name)
        if tt_folder is None:
            create_name = _tt_folder_create_name(project, tt_name)
            tt_folder = await self._ensure_subfolder(project_root, create_name)

        tt_id = _disk_folder_id(tt_folder)
        if not tt_id:
            raise FinalReportError(f"Не удалось получить папку ТТ «{tt_name}» на диске.")

        fo_folder = await self._ensure_subfolder(tt_id, fo_name)
        fo_id = _disk_folder_id(fo_folder)
        if not fo_id:
            raise FinalReportError(f"Не удалось создать папку «{fo_name}» внутри ТТ.")
        logger.info("FO files → %r inside TT %r (id=%s)", fo_name, tt_name, tt_id)
        return fo_id

    async def _locate_tt_folder(
        self,
        project_root: str,
        project: str,
        tt_name: str,
    ) -> dict[str, Any] | None:
        legacy = f"{project} {tt_name}".strip()
        create_name = _tt_folder_create_name(project, tt_name)
        seen: set[str] = set()
        for candidate in (tt_name, create_name, legacy):
            key = candidate.casefold().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            found = await self._find_child_folder(project_root, candidate)
            if found is not None:
                return found
        return await self._find_tt_folder_by_tokens(project_root, tt_name)

    async def _find_tt_folder_by_tokens(
        self,
        parent_id: str,
        tt_name: str,
    ) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        best_score = -1
        async for item in self._iter_child_folders(parent_id):
            name = str(item.get("NAME") or item.get("name") or "")
            if not _tt_folder_tokens_match(name, tt_name):
                continue
            score = _tt_folder_match_score(name, tt_name)
            if score > best_score:
                best = item
                best_score = score
        return best

    async def _iter_child_folders(self, parent_id: str):
        start = 0
        while True:
            result = await self._call(
                "disk.folder.getchildren",
                {"id": parent_id, "start": start},
            )
            items = result if isinstance(result, list) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("TYPE") or item.get("type") or "").casefold()
                if not item_type or item_type == "folder":
                    yield item
            if len(items) < 50:
                break
            start += 50

    async def _ensure_subfolder(self, parent_id: str, name: str) -> dict[str, Any]:
        existing = await self._find_child_folder(parent_id, name)
        if existing is not None:
            return existing
        try:
            result = await self._call(
                "disk.folder.addsubfolder",
                {
                    "id": parent_id,
                    "data": {"NAME": name},
                },
            )
        except FinalReportError:
            # Возможно, папка уже есть (гонка / другая кодировка ответа)
            existing = await self._find_child_folder(parent_id, name)
            if existing is not None:
                return existing
            raise
        if isinstance(result, dict):
            return result
        raise FinalReportError("Не удалось создать папку на диске Bitrix.")

    async def _find_child_folder(self, parent_id: str, name: str) -> dict[str, Any] | None:
        start = 0
        needle = name.casefold()
        while True:
            result = await self._call(
                "disk.folder.getchildren",
                {
                    "id": parent_id,
                    "filter": {"NAME": name},
                    "start": start,
                },
            )
            items = result if isinstance(result, list) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_name = str(item.get("NAME") or item.get("name") or "")
                item_type = str(item.get("TYPE") or item.get("type") or "").casefold()
                if item_name.casefold() == needle and (
                    not item_type or item_type == "folder"
                ):
                    return item
            if len(items) < 50:
                break
            start += 50
        return None

    async def _upload_file(self, folder_id: str, filename: str, content: bytes) -> None:
        safe_name = _safe_filename(filename)
        # Надёжный способ: получить UploadUrl и отправить multipart (без base64 в JSON).
        upload_info = await self._call(
            "disk.folder.uploadfile",
            {
                "id": folder_id,
                "data": {"NAME": safe_name},
                "generateUniqueName": True,
            },
        )
        if not isinstance(upload_info, dict):
            raise FinalReportError("Bitrix не вернул URL для загрузки файла.")
        upload_url = str(upload_info.get("uploadUrl") or upload_info.get("UPLOAD_URL") or "")
        field = str(upload_info.get("field") or upload_info.get("FIELD") or "file")
        if not upload_url:
            # fallback: старый способ через base64
            payload = base64.b64encode(content).decode("ascii")
            await self._call(
                "disk.folder.uploadfile",
                {
                    "id": folder_id,
                    "data": {"NAME": safe_name},
                    "fileContent": [safe_name, payload],
                    "generateUniqueName": True,
                },
            )
            return
        timeout = aiohttp.ClientTimeout(total=600)
        form = aiohttp.FormData()
        form.add_field(
            field,
            content,
            filename=safe_name,
            content_type="application/octet-stream",
        )
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(upload_url, data=form) as response:
                body = await response.text()
                if response.status >= 400:
                    raise FinalReportError(
                        f"Не удалось загрузить фото на диск Bitrix (HTTP {response.status})."
                    )
                try:
                    data = json.loads(body) if body else {}
                except json.JSONDecodeError:
                    data = {}
                if isinstance(data, dict) and data.get("error"):
                    raise FinalReportError(
                        f"Bitrix API: {data.get('error_description') or data.get('error')}"
                    )

    async def _create_task(
        self,
        *,
        title: str,
        description: str,
        responsible_id: int,
        created_by_id: int,
        auditor_ids: list[int],
        group_id: int,
        deadline: str,
        crm_items: list[str] | None = None,
    ) -> dict[str, Any]:
        """Создаёт задачу ФО.

        Bitrix не даёт webhook'у 401 сразу указать чужого CREATED_BY и чужого
        исполнителя. Рабочая схема: создать от Титкова (он же временно исполнитель)
        → сменить постановщика на менеджера ТТ → назначить реального исполнителя,
        проект, наблюдателей и CRM.
        """
        api_user_id = self._settings.bitrix_fo_fallback_creator_id
        originator_id = created_by_id if created_by_id > 0 else api_user_id

        seed = await self._call(
            "tasks.task.add",
            {
                "fields": {
                    "TITLE": title,
                    "DESCRIPTION": description,
                    "DESCRIPTION_IN_BBCODE": "N",
                    "RESPONSIBLE_ID": api_user_id,
                    "CREATED_BY": api_user_id,
                    "DEADLINE": deadline,
                    "PRIORITY": 1,
                }
            },
            json_body=True,
        )
        task = seed.get("task") if isinstance(seed, dict) else None
        if not isinstance(task, dict):
            raise FinalReportError("Не удалось создать задачу в Bitrix.")
        task_id = str(task.get("id") or task.get("ID") or "")
        if not task_id:
            raise FinalReportError("Задача создана, но Bitrix не вернул ID.")

        if originator_id != api_user_id:
            try:
                await self._call(
                    "tasks.task.update",
                    {"taskId": task_id, "fields": {"CREATED_BY": originator_id}},
                    json_body=True,
                )
            except FinalReportError as exc:
                logger.warning(
                    "FO cannot set CREATED_BY=%s (%s), keep Titkov %s",
                    originator_id,
                    exc,
                    api_user_id,
                )
                originator_id = api_user_id

        auditors = [user_id for user_id in auditor_ids if user_id != originator_id]
        finalize: dict[str, Any] = {
            "RESPONSIBLE_ID": responsible_id,
            "GROUP_ID": group_id,
        }
        if auditors:
            finalize["AUDITORS"] = auditors
        if crm_items:
            finalize["UF_CRM_TASK"] = crm_items
        try:
            result = await self._call(
                "tasks.task.update",
                {"taskId": task_id, "fields": finalize},
                json_body=True,
            )
        except FinalReportError:
            if not crm_items:
                raise
            finalize.pop("UF_CRM_TASK", None)
            logger.warning(
                "FO finalize with CRM %s failed, retry without CRM", crm_items
            )
            result = await self._call(
                "tasks.task.update",
                {"taskId": task_id, "fields": finalize},
                json_body=True,
            )
        if isinstance(result, dict):
            updated = result.get("task")
            if isinstance(updated, dict):
                return updated
        got = await self._call(
            "tasks.task.get",
            {
                "taskId": task_id,
                "select": [
                    "ID",
                    "TITLE",
                    "CREATED_BY",
                    "RESPONSIBLE_ID",
                    "GROUP_ID",
                    "UF_CRM_TASK",
                    "AUDITORS",
                ],
            },
        )
        if isinstance(got, dict):
            task = got.get("task")
            if isinstance(task, dict):
                return task
        return {"id": task_id}
    async def _find_crm_task_bindings(self, project: str, store_name: str) -> list[str]:
        """Ищет CRM-объект ТТ в SPA (не через чужие задачи — там часто чужие привязки)."""
        entity_type_id = int(self._settings.bitrix_fo_crm_entity_type_id)
        prefix = (self._settings.bitrix_fo_crm_binding_prefix or f"T{entity_type_id}").strip()
        if prefix.endswith("_"):
            prefix = prefix[:-1]
        needles = _crm_search_needles(project, store_name)
        best_id: int | None = None
        best_score = 0
        best_title = ""
        for needle in needles:
            try:
                result = await bitrix_call(
                    self._settings,
                    "crm.item.list",
                    {
                        "entityTypeId": entity_type_id,
                        "select": ["id", "title"],
                        "filter": {"%title": needle},
                        "order": {"id": "DESC"},
                        "start": 0,
                    },
                    timeout_seconds=45,
                    json_body=True,
                )
            except RuntimeError:
                logger.exception(
                    "CRM SPA lookup failed entityTypeId=%s needle=%r",
                    entity_type_id,
                    needle,
                )
                continue
            items = result.get("items") if isinstance(result, dict) else result
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or item.get("TITLE") or "")
                score = _crm_item_match_score(title, project, store_name)
                if score <= 0:
                    continue
                try:
                    item_id = int(item.get("id") or item.get("ID") or 0)
                except (TypeError, ValueError):
                    continue
                if item_id <= 0:
                    continue
                if score > best_score or (
                    score == best_score and (best_id is None or item_id > best_id)
                ):
                    best_score = score
                    best_id = item_id
                    best_title = title
            if best_score >= 1000:
                break
        if best_id is None:
            logger.info(
                "CRM object for project=%s store=%r not found in SPA %s",
                project,
                store_name,
                entity_type_id,
            )
            return []
        binding = f"{prefix}_{best_id}"
        logger.info(
            "CRM object for project=%s store=%r -> %s (%r, score=%s)",
            project,
            store_name,
            binding,
            best_title,
            best_score,
        )
        return [binding]

    async def _call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        json_body: bool = False,
    ) -> Any:
        try:
            return await bitrix_call(
                self._settings,
                method,
                params,
                timeout_seconds=120,
                json_body=json_body,
            )
        except RuntimeError as exc:
            raise FinalReportError(str(exc)) from exc


def _fo_upload_folder_name(project: str, store_name: str) -> str:
    return f"Итоговый фотоотчет {project} {store_name.strip()}"


def _name_tokens(value: str) -> list[str]:
    return [part for part in value.split() if part.strip()]


def _crm_search_needles(project: str, store_name: str) -> list[str]:
    store = store_name.strip()
    needles = [store, f"{project} {store}".strip()]
    parts = _name_tokens(store)
    if len(parts) >= 2 and parts[0].casefold() == project.casefold():
        needles.append(" ".join(parts[1:]))
    unique: list[str] = []
    for needle in needles:
        item = needle.strip()
        if item and item not in unique:
            unique.append(item)
    return unique


def _normalize_crm_item_title(title: str) -> str:
    text = (title or "").strip().casefold()
    # « Пороховской (Шелфбанеры)» → «пороховской»
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()
    return text


def _store_name_variants(project: str, store_name: str) -> list[str]:
    store = store_name.strip().casefold()
    variants = [store]
    parts = _name_tokens(store_name)
    if len(parts) >= 2 and parts[0].casefold() == project.casefold():
        bare = " ".join(parts[1:]).casefold().strip()
        if bare and bare not in variants:
            variants.append(bare)
    return variants


def _crm_item_match_score(title: str, project: str, store_name: str) -> int:
    """Строгое соответствие CRM-объекта ТТ: без чужих ТТ из общих задач."""
    base = _normalize_crm_item_title(title)
    if not base:
        return 0
    variants = _store_name_variants(project, store_name)
    for variant in variants:
        if not variant:
            continue
        if base == variant:
            return 1000
        if base.startswith(f"{variant} ") or base.endswith(f" {variant}"):
            return 800
        # Целое слово/фраза, не кусок чужого названия.
        pattern = rf"(?:^|[\s\-_/\"«]){re.escape(variant)}(?:$|[\s\-_/\"»,.(])"
        if re.search(pattern, base):
            return 500
    return 0


def _tt_folder_tokens_match(folder_name: str, tt_name: str) -> bool:
    left = folder_name.casefold().strip()
    right = tt_name.casefold().strip()
    if left == right:
        return True
    folder_parts = {part.casefold() for part in _name_tokens(folder_name)}
    tt_parts = [part.casefold() for part in _name_tokens(tt_name)]
    if not tt_parts or not folder_parts:
        return False
    if all(part in folder_parts for part in tt_parts):
        return True
    if len(folder_parts) == 1:
        only = next(iter(folder_parts))
        if only in tt_parts:
            return True
    return False


def _tt_folder_match_score(folder_name: str, tt_name: str) -> int:
    if folder_name.casefold().strip() == tt_name.casefold().strip():
        return 10_000
    folder_parts = set(_name_tokens(folder_name))
    tt_parts = set(_name_tokens(tt_name))
    overlap = len({p.casefold() for p in folder_parts} & {p.casefold() for p in tt_parts})
    return overlap * 100 - abs(len(folder_parts) - len(tt_parts))


def _tt_folder_create_name(project: str, tt_name: str) -> str:
    """Новая папка ТТ без лишнего префикса проекта («МФ …» → «…»)."""
    parts = _name_tokens(tt_name)
    if len(parts) >= 2 and parts[0].casefold() == project.casefold():
        return " ".join(parts[1:])
    return tt_name.strip()


def _disk_folder_id(folder: dict[str, Any]) -> str:
    return str(
        folder.get("REAL_OBJECT_ID")
        or folder.get("realObjectId")
        or folder.get("ID")
        or folder.get("id")
        or ""
    )


def _deadline_for_fo() -> str:
    """До 16:30 МСК — сегодня 18:00; после 16:30 — завтра 18:00.

    Если крайний срок выпадает на субботу или воскресенье — перенос на
    понедельник 18:00 МСК.
    """
    now = datetime.now(_MSK)
    cutoff = now.replace(hour=16, minute=30, second=0, microsecond=0)
    day = now.date() if now <= cutoff else (now.date() + timedelta(days=1))
    if day.weekday() == 5:  # суббота → понедельник
        day += timedelta(days=2)
    elif day.weekday() == 6:  # воскресенье → понедельник
        day += timedelta(days=1)
    return f"{day.isoformat()}T18:00:00+03:00"


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w.\- ()а-яА-ЯёЁ]+", "_", name, flags=re.UNICODE).strip("._ ")
    return cleaned or "photo.jpg"
