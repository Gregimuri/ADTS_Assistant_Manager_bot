from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin

import aiohttp

from app.config import Settings
from app.services.catalog import Catalog
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
    def __init__(self, settings: Settings, catalog: Catalog) -> None:
        self._settings = settings
        self._catalog = catalog
        self._base_url = settings.bitrix_webhook_url.rstrip("/") + "/"

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
            raise FinalReportError("Нужно хотя бы одно фото.")
        if not self._settings.bitrix_webhook_url.strip():
            raise FinalReportError("BITRIX_WEBHOOK_URL не задан.")

        project = self.resolve_project(project)
        group_id = FO_PROJECT_GROUPS[project]
        folder_name = f"{project} {store.name}".strip()

        parent_id = await self._resolve_parent_folder(group_id)
        folder = await self._ensure_subfolder(parent_id, folder_name)
        folder_id = str(folder.get("ID") or folder.get("id") or "")
        if not folder_id:
            raise FinalReportError("Bitrix не вернул ID созданной папки.")

        uploaded = 0
        for index, photo in enumerate(photos, start=1):
            content: bytes = await download_photo(photo.file_id)
            if not content:
                continue
            filename = photo.filename or f"photo_{index}.jpg"
            await self._upload_file(folder_id, filename, content)
            uploaded += 1
        if uploaded == 0:
            raise FinalReportError("Не удалось скачать фото из Telegram.")

        folder_url = _FOLDER_LINK.format(folder_id=folder_id)
        creator_id, creator_name = await self._resolve_creator(store.manager)
        deadline = _deadline_tomorrow()
        description = f"Ссылка на диск: {folder_url}"
        task = await self._create_task(
            title=f'Финальный отчет - "{store.name}"',
            description=description,
            responsible_id=self._settings.bitrix_fo_responsible_id,
            creator_id=creator_id,
            auditor_ids=sorted(self._settings.bitrix_fo_auditor_ids),
            group_id=group_id,
            deadline=deadline,
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
            folder_name=folder_name,
            photos_uploaded=uploaded,
            creator_name=creator_name,
            store_name=store.name,
            project=project,
        )

    async def _resolve_parent_folder(self, group_id: int) -> str:
        """Корень диска Bitrix-группы проекта."""
        try:
            result = await self._call(
                "disk.storage.getlist",
                {
                    "filter": {
                        "ENTITY_TYPE": "group",
                        "ENTITY_ID": group_id,
                    }
                },
            )
        except FinalReportError as exc:
            if "insufficient_scope" in str(exc).casefold() or "disk" in str(exc).casefold():
                raise FinalReportError(
                    "У webhook Bitrix нет права Disk. "
                    "Добавьте доступ «Диск» (disk) во входящий webhook и повторите."
                ) from exc
            raise
        items = result if isinstance(result, list) else []
        if not items and isinstance(result, dict):
            items = result.get("storages") or result.get("result") or []
        if not isinstance(items, list) or not items:
            raise FinalReportError(
                f"Не найден диск Bitrix-группы {group_id}. Проверьте права webhook на Disk."
            )
        root_id = str(items[0].get("ROOT_OBJECT_ID") or items[0].get("rootObjectId") or "")
        if not root_id:
            raise FinalReportError("У диска группы нет ROOT_OBJECT_ID.")
        return root_id

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
        payload = base64.b64encode(content).decode("ascii")
        await self._call(
            "disk.folder.uploadfile",
            {
                "id": folder_id,
                "data": {"NAME": safe_name},
                "fileContent": [safe_name, payload],
            },
        )

    async def _resolve_creator(self, manager_name: str) -> tuple[int, str]:
        fallback_id = self._settings.bitrix_fo_fallback_creator_id
        fallback_name = "Титков Григорий"
        cleaned = (manager_name or "").strip()
        if not cleaned:
            return fallback_id, fallback_name
        user = await self._find_user_by_name(cleaned)
        if user is None:
            logger.info("Manager %r not found in Bitrix, fallback to Titkov", cleaned)
            return fallback_id, fallback_name
        user_id = int(user.get("ID") or user.get("id") or 0)
        full_name = _format_user_name(user)
        if user_id <= 0:
            return fallback_id, fallback_name
        return user_id, full_name or cleaned

    async def _find_user_by_name(self, manager_name: str) -> dict[str, Any] | None:
        result = await self._call(
            "user.search",
            {"FILTER": {"FIND": manager_name}},
        )
        users = result if isinstance(result, list) else []
        if not users:
            # Попробуем переставить фамилию/имя
            parts = manager_name.split()
            if len(parts) >= 2:
                swapped = f"{parts[-1]} {' '.join(parts[:-1])}"
                result = await self._call("user.search", {"FILTER": {"FIND": swapped}})
                users = result if isinstance(result, list) else []
        best: dict[str, Any] | None = None
        for user in users:
            if not isinstance(user, dict):
                continue
            if user.get("ACTIVE") is False:
                continue
            full = _format_user_name(user)
            if _names_equivalent(full, manager_name) or _person_contains(full, manager_name):
                return user
            if best is None:
                best = user
        return best

    async def _create_task(
        self,
        *,
        title: str,
        description: str,
        responsible_id: int,
        creator_id: int,
        auditor_ids: list[int],
        group_id: int,
        deadline: str,
    ) -> dict[str, Any]:
        result = await self._call(
            "tasks.task.add",
            {
                "fields": {
                    "TITLE": title,
                    "DESCRIPTION": description,
                    "DESCRIPTION_IN_BBCODE": "N",
                    "RESPONSIBLE_ID": responsible_id,
                    "CREATED_BY": creator_id,
                    "AUDITORS": auditor_ids,
                    "GROUP_ID": group_id,
                    "DEADLINE": deadline,
                    "PRIORITY": "1",
                }
            },
        )
        if isinstance(result, dict):
            task = result.get("task")
            if isinstance(task, dict):
                return task
            return result
        raise FinalReportError("Не удалось создать задачу в Bitrix.")

    async def _call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        url = urljoin(self._base_url, method)
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=params or {}) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
        if not isinstance(data, dict):
            raise FinalReportError("Bitrix вернул неожиданный ответ.")
        if data.get("error"):
            error = str(data.get("error") or "")
            description = str(data.get("error_description") or error)
            raise FinalReportError(f"Bitrix API: {description}")
        return data.get("result")


def _deadline_tomorrow() -> str:
    day = datetime.now(_MSK).date() + timedelta(days=1)
    return f"{day.isoformat()}T18:00:00+03:00"


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w.\- ()а-яА-ЯёЁ]+", "_", name, flags=re.UNICODE).strip("._ ")
    return cleaned or "photo.jpg"


def _format_user_name(user: dict[str, Any]) -> str:
    last = str(user.get("LAST_NAME") or user.get("lastName") or "").strip()
    first = str(user.get("NAME") or user.get("name") or "").strip()
    return " ".join(part for part in (last, first) if part).strip()


def _names_equivalent(left: str, right: str) -> bool:
    return _normalize_person(left) == _normalize_person(right)


def _normalize_person(value: str) -> str:
    parts = [part for part in re.split(r"\s+", value.strip().casefold()) if part]
    return " ".join(sorted(parts))


def _person_contains(full_name: str, query: str) -> bool:
    full_parts = set(_normalize_person(full_name).split())
    query_parts = set(_normalize_person(query).split())
    if not query_parts:
        return False
    return query_parts.issubset(full_parts)
