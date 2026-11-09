"""Сдача ФО в платформу: колонка со ссылкой + файлы в открытую задачу."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import aiohttp

from app.config import Settings
from app.services.bitrix_rest import ssl_connector
from app.services.sheets import ProjectStore

logger = logging.getLogger(__name__)

_TRANSIENT_NET = (
    aiohttp.ClientConnectorError,
    aiohttp.ClientOSError,
    aiohttp.ServerDisconnectedError,
    asyncio.TimeoutError,
)
_CALL_ATTEMPTS = 4


@dataclass(frozen=True, slots=True)
class PlatformPoint:
    row_id: str
    name: str
    region: str = ""
    address: str = ""
    manager: str = ""


@dataclass(frozen=True, slots=True)
class PlatformFoSubmit:
    point_name: str
    column_id: str
    folder_url: str
    photos: int
    platform_url: str
    bitrix_task_id: int | None = None

_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_PAGE = 100
_PROMPT_POINTS = 20
_POINT_COLUMNS = "point_name,region,address,status,manager"


def _point_name_key(value: str) -> str:
    return " ".join(value.casefold().split())


def _parse_points(page: dict[str, Any]) -> list[PlatformPoint]:
    """Строки ответа workbook.rows.get → точки платформы."""
    raw_items = page.get("items") or []
    if not isinstance(raw_items, list):
        return []
    points: list[PlatformPoint] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("point_name") or "").strip()
        row_id = str(item.get("row_id") or "").strip()
        if not name or not row_id:
            continue
        points.append(
            PlatformPoint(
                row_id=row_id,
                name=name,
                region=str(item.get("region") or ""),
                address=str(item.get("address") or ""),
                manager=str(item.get("manager") or ""),
            )
        )
    return points


def _page_total(page: dict[str, Any]) -> int:
    try:
        return max(0, int(page.get("total") or 0))
    except (TypeError, ValueError):
        return 0


class AdtsPlatformError(RuntimeError):
    """Платформа не приняла ФО."""


class AdtsPlatformClient:
    def __init__(self, settings: Settings, hook_url: str | None = None) -> None:
        self._settings = settings
        self._hook_url = (hook_url if hook_url is not None else settings.adts_hook_url).strip()

    def enabled(self) -> bool:
        return bool(self._hook_url)

    async def ping(self) -> dict[str, Any]:
        """Проверка хука: схема и число точек. Bitrix не вызывает."""
        if not self.enabled():
            raise AdtsPlatformError("Хук платформы не задан.")
        schema = await self._call("workbook.schema.get", {})
        if not isinstance(schema, dict):
            raise AdtsPlatformError("Платформа не вернула схему.")
        columns = schema.get("columns") if isinstance(schema.get("columns"), list) else []
        fo_raw = (self._settings.adts_fo_link_column or "").strip()
        fo_column = "нет"
        wanted = fo_raw.casefold()
        for item in columns:
            if not isinstance(item, dict):
                continue
            col_id = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            if col_id == fo_raw or col_id.casefold() == wanted or title.casefold() == wanted:
                fo_column = title or col_id
                break
        total, _ = await self.sample_points(limit=1)
        return {
            "project_id": schema.get("project_id") or "—",
            "columns": len(columns),
            "points": total,
            "fo_column": fo_column,
        }

    async def _rows_page(
        self,
        *,
        q: str | None = None,
        limit: int = _PAGE,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Одна страница workbook.rows.get с фильтром и только нужными колонками."""
        body: dict[str, Any] = {
            "limit": max(1, min(limit, _PAGE)),
            "offset": max(0, offset),
            "columns": _POINT_COLUMNS,
        }
        query = (q or "").strip()
        if query:
            body["q"] = query
        page = await self._call("workbook.rows.get", body)
        return page if isinstance(page, dict) else {}

    async def sample_points(
        self,
        limit: int = _PROMPT_POINTS,
    ) -> tuple[int, list[PlatformPoint]]:
        """Несколько точек для подсказки + общее число строк."""
        page = await self._rows_page(limit=limit)
        return _page_total(page), _parse_points(page)

    async def find_point(self, query: str) -> PlatformPoint:
        """Одна точка по названию. Несколько совпадений — ошибка."""
        wanted = _point_name_key(query)
        if not wanted:
            raise AdtsPlatformError("Введите название ТТ.")
        page = await self._rows_page(q=query, limit=_PAGE)
        candidates = _parse_points(page)
        total = _page_total(page)
        matched = [item for item in candidates if _point_name_key(item.name) == wanted]
        if not matched:
            matched = [item for item in candidates if wanted in _point_name_key(item.name)]
        names = {item.name for item in matched}
        if not names:
            if total > len(candidates):
                raise AdtsPlatformError(
                    "Слишком много совпадений. Уточните название ТТ."
                )
            raise AdtsPlatformError(f"Точка «{query.strip()}» не найдена в таблице платформы.")
        if len(names) > 1:
            sample = ", ".join(sorted(names)[:5])
            raise AdtsPlatformError(f"Найдено несколько ТТ. Уточните название.\nПодходят: {sample}")
        return matched[0]

    async def resolve_fo_column_id(self) -> str:
        """Id колонки ссылки ФО: точный id или заголовок из настройки."""
        raw = (self._settings.adts_fo_link_column or "").strip()
        if not raw:
            raise AdtsPlatformError("ADTS_FO_LINK_COLUMN не задан.")
        schema = await self._call("workbook.schema.get", {})
        columns = schema.get("columns") if isinstance(schema, dict) else None
        if not isinstance(columns, list):
            raise AdtsPlatformError("Платформа не вернула схему таблицы.")
        wanted = raw.casefold()
        for item in columns:
            if not isinstance(item, dict):
                continue
            col_id = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            if col_id == raw or col_id.casefold() == wanted:
                return col_id
            if title.casefold() == wanted:
                return col_id
        raise AdtsPlatformError(f"Колонка «{raw}» не найдена в таблице платформы.")

    async def write_row_cells(self, row_id: str, cells: dict[str, str]) -> None:
        """Пишет ячейки точки до сдачи результата — следующая задача увидит ссылку."""
        if not row_id.strip() or not cells:
            raise AdtsPlatformError("Нечего писать в таблицу.")
        await self._call(
            "workbook.rows.update",
            {
                "mode": "upsert",
                "rows": [{"row_id": row_id, "cells": cells}],
            },
        )

    async def submit_result_for_point(
        self,
        *,
        point_name: str,
        files: list[tuple[str, bytes]],
        result_text: str = "Фотоотчёт",
        folder_url: str = "",
        write_link: bool = True,
    ) -> PlatformFoSubmit:
        """Сдаёт файлы в открытую задачу; ссылку в колонку пишет до result (для spawn/Bitrix ФО)."""
        if not self.enabled():
            raise AdtsPlatformError("Хук платформы не задан.")
        point = await self.find_point(point_name)
        column_id = await self.resolve_fo_column_id()
        link = (folder_url or self._settings.adts_fo_test_folder_url or "").strip()
        if not link:
            raise AdtsPlatformError("Нет ссылки для колонки ФО.")
        payload = [
            (name, content)
            for name, content in files
            if PurePosixPath(name).suffix.casefold() in _IMAGE_EXT
        ]
        if not payload:
            raise AdtsPlatformError("Нужен хотя бы один файл изображения.")
        # Ссылку до result: complete/spawn шага ФО подставляет $Ссылка на ФО$ из таблицы.
        if write_link:
            await self.write_row_cells(point.row_id, {column_id: link})
        created = await self._call(
            "task.result",
            {
                "point_name": point.name,
                "result_text": result_text or "Фотоотчёт",
                "files": [
                    {
                        "filename": name,
                        "content_base64": base64.b64encode(content).decode("ascii"),
                    }
                    for name, content in payload
                ],
            },
        )
        number = created.get("number") if isinstance(created, dict) else None
        template = (self._settings.adts_task_url_template or "").strip()
        if template and isinstance(number, int):
            platform_url = template.format(number=number)
        elif isinstance(number, int):
            platform_url = f"№{number}"
        else:
            platform_url = "сдано"
        bitrix_raw = created.get("bitrix_task_id") if isinstance(created, dict) else None
        bitrix_task_id: int | None = None
        if bitrix_raw is not None and str(bitrix_raw).strip():
            try:
                bitrix_task_id = int(bitrix_raw)
            except (TypeError, ValueError):
                bitrix_task_id = None
        return PlatformFoSubmit(
            point_name=point.name,
            column_id=column_id,
            folder_url=link,
            photos=len(payload),
            platform_url=platform_url,
            bitrix_task_id=bitrix_task_id,
        )

    async def write_fo_link_for_point(self, *, point_name: str, folder_url: str) -> str:
        """Пишет ссылку ФО в колонку точки. Возвращает id колонки."""
        if not self.enabled():
            raise AdtsPlatformError("Хук платформы не задан.")
        link = (folder_url or self._settings.adts_fo_test_folder_url or "").strip()
        if not link:
            raise AdtsPlatformError("Нет ссылки для колонки ФО.")
        point = await self.find_point(point_name)
        column_id = await self.resolve_fo_column_id()
        await self.write_row_cells(point.row_id, {column_id: link})
        return column_id

    async def submit_fo(
        self,
        *,
        store: ProjectStore,
        title: str,
        files: list[tuple[str, bytes]],
        urgent: bool = False,
    ) -> str:
        """Создаёт задачу на точку и сдаёт картинки. Возвращает URL карточки."""
        if not self.enabled():
            raise AdtsPlatformError("ADTS_HOOK_URL не задан.")
        row_id = await self._find_row_id(store.name)
        description = (
            f"ФО из Telegram-бота.\n"
            f"Проект: {store.project}\n"
            f"ТТ: {store.name}\n"
            f"Адрес: {store.address}"
        )
        if urgent:
            description = "СРОЧНО.\n" + description
        add_body: dict[str, Any] = {
            "title": title,
            "description": description,
            "row_id": row_id,
        }
        if self._settings.adts_assignee_user_id:
            add_body["assignee_user_id"] = self._settings.adts_assignee_user_id
        if self._settings.adts_creator_user_id:
            add_body["creator_user_id"] = self._settings.adts_creator_user_id
        created = await self._call("task.add", add_body)
        number = created.get("number") if isinstance(created, dict) else None
        if not isinstance(number, int):
            raise AdtsPlatformError("Платформа не вернула номер задачи.")
        payload = [
            (name, content)
            for name, content in files
            if PurePosixPath(name).suffix.casefold() in _IMAGE_EXT
        ]
        if payload:
            await self._call(
                "task.result",
                {
                    "task_number": number,
                    "result_text": "Фотоотчёт",
                    "files": [
                        {
                            "filename": name,
                            "content_base64": base64.b64encode(content).decode("ascii"),
                        }
                        for name, content in payload
                    ],
                },
            )
        else:
            logger.warning("FO to platform: no image files for task %s", number)
        template = (self._settings.adts_task_url_template or "").strip()
        if template:
            return template.format(number=number)
        return f"№{number}"

    async def _find_row_id(self, point_name: str) -> str:
        point = await self.find_point(point_name)
        return point.row_id

    async def _call(self, method: str, body: dict[str, Any]) -> Any:
        url = f"{self._hook_url.rstrip('/')}/{method}"
        timeout = aiohttp.ClientTimeout(total=120)
        last_error: Exception | None = None
        for attempt in range(1, _CALL_ATTEMPTS + 1):
            try:
                async with aiohttp.ClientSession(
                    timeout=timeout,
                    connector=ssl_connector(),
                ) as session:
                    async with session.post(url, json=body) as response:
                        text = await response.text()
                        try:
                            data = json.loads(text) if text else {}
                        except json.JSONDecodeError:
                            data = {"raw": text[:500]}
                        if response.status >= 400:
                            detail = data.get("error") if isinstance(data, dict) else data
                            if isinstance(detail, dict):
                                detail = (
                                    detail.get("message")
                                    or detail.get("details")
                                    or detail
                                )
                            raise AdtsPlatformError(
                                f"{method} HTTP {response.status}: {detail}"
                            )
                        return data
            except AdtsPlatformError:
                raise
            except _TRANSIENT_NET as exc:
                last_error = exc
                logger.warning(
                    "platform %s attempt %s/%s failed: %s",
                    method,
                    attempt,
                    _CALL_ATTEMPTS,
                    exc,
                )
                if attempt < _CALL_ATTEMPTS:
                    await asyncio.sleep(min(2 ** (attempt - 1), 8))
        raise AdtsPlatformError(
            f"Не удалось связаться с платформой ({method}): {last_error}"
        ) from last_error
