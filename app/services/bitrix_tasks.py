from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin

import aiohttp

from app.config import Settings
from app.services.dates import parse_ru_date

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BitrixTask:
    task_id: str
    title: str
    description: str
    status: int
    real_status: int
    created_date: date | None
    closed_date: date | None
    responsible_name: str
    creator_name: str


class BitrixTasksError(RuntimeError):
    """Не удалось загрузить задачи из Bitrix."""


class BitrixTasksClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.bitrix_webhook_url.rstrip("/") + "/"

    async def list_assembly_tasks(self) -> list[BitrixTask]:
        if not self._settings.bitrix_webhook_url.strip():
            raise BitrixTasksError("BITRIX_WEBHOOK_URL не задан.")

        raw_tasks = await self._fetch_all_tasks()
        matched = [task for task in raw_tasks if _matches_assembly_task(task, self._settings)]
        logger.info("Loaded %s assembly tasks from Bitrix", len(matched))
        return matched

    async def _fetch_all_tasks(self) -> list[BitrixTask]:
        tasks: list[BitrixTask] = []
        start = 0
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while True:
                payload = await self._call(
                    session,
                    "tasks.task.list",
                    {
                        "select": [
                            "ID",
                            "TITLE",
                            "DESCRIPTION",
                            "STATUS",
                            "REAL_STATUS",
                            "CREATED_DATE",
                            "CLOSED_DATE",
                            "RESPONSIBLE_NAME",
                            "CREATED_BY_NAME",
                        ],
                        "start": start,
                    },
                )
                batch = payload.get("tasks") or []
                for item in batch:
                    parsed = _parse_task(item)
                    if parsed is not None:
                        tasks.append(parsed)
                next_start = payload.get("next")
                if next_start is None:
                    break
                start = int(next_start)
        return tasks

    async def _call(
        self,
        session: aiohttp.ClientSession,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        url = urljoin(self._base_url, method)
        async with session.get(url, params=_flatten_params(params)) as response:
            response.raise_for_status()
            data = await response.json(content_type=None)
        if not isinstance(data, dict):
            raise BitrixTasksError("Bitrix вернул неожиданный ответ.")
        if data.get("error"):
            raise BitrixTasksError(f"Bitrix API: {data.get('error_description') or data['error']}")
        result = data.get("result")
        if not isinstance(result, dict):
            raise BitrixTasksError("Bitrix API: пустой result.")
        return result


def count_open_assembly_tasks(tasks: list[BitrixTask]) -> int:
    return sum(1 for task in tasks if _is_open(task))


def count_open_assembly_before_today(tasks: list[BitrixTask], today: date) -> int:
    return sum(
        1
        for task in tasks
        if _is_open(task) and task.created_date is not None and task.created_date < today
    )


def count_assembly_created_today(tasks: list[BitrixTask], today: date) -> int:
    return sum(1 for task in tasks if task.created_date == today)


def count_assembly_completed_today(tasks: list[BitrixTask], today: date) -> int:
    return sum(
        1
        for task in tasks
        if not _is_open(task) and task.closed_date == today
    )


def _matches_assembly_task(task: BitrixTask, settings: Settings) -> bool:
    text = f"{task.title} {task.description}".casefold()
    if "сборка" not in text:
        return False
    responsible = settings.bitrix_assembly_responsible.casefold()
    creator = settings.bitrix_assembly_creator.casefold()
    if responsible not in task.responsible_name.casefold():
        return False
    if creator not in task.creator_name.casefold():
        return False
    return True


def _is_open(task: BitrixTask) -> bool:
    return task.real_status != 5 and task.status != 5


def _parse_task(raw: dict[str, Any]) -> BitrixTask | None:
    task_id = str(raw.get("id") or raw.get("ID") or "").strip()
    if not task_id:
        return None
    responsible_name = _person_name(raw.get("responsible") or raw.get("RESPONSIBLE"))
    if not responsible_name:
        responsible_name = str(raw.get("responsibleName") or raw.get("RESPONSIBLE_NAME") or "")
    creator_name = _person_name(raw.get("creator") or raw.get("CREATOR"))
    if not creator_name:
        creator_name = str(raw.get("createdByName") or raw.get("CREATED_BY_NAME") or "")
    return BitrixTask(
        task_id=task_id,
        title=str(raw.get("title") or raw.get("TITLE") or ""),
        description=str(raw.get("description") or raw.get("DESCRIPTION") or ""),
        status=int(raw.get("status") or raw.get("STATUS") or 0),
        real_status=int(raw.get("realStatus") or raw.get("REAL_STATUS") or 0),
        created_date=_parse_bitrix_date(raw.get("createdDate") or raw.get("CREATED_DATE")),
        closed_date=_parse_bitrix_date(raw.get("closedDate") or raw.get("CLOSED_DATE")),
        responsible_name=responsible_name,
        creator_name=creator_name,
    )


def _person_name(value: object) -> str:
    if isinstance(value, dict):
        for key in ("name", "NAME", "lastName", "LAST_NAME"):
            part = value.get(key)
            if part:
                return str(part).strip()
        first = str(value.get("name") or value.get("NAME") or "").strip()
        last = str(value.get("lastName") or value.get("LAST_NAME") or "").strip()
        return " ".join(part for part in (first, last) if part).strip()
    return str(value or "").strip()


def _parse_bitrix_date(value: object) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    parsed = parse_ru_date(text)
    if parsed is not None:
        return parsed
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _flatten_params(params: dict[str, Any], prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in params.items():
        full_key = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, list):
            for index, item in enumerate(value):
                flat[f"{full_key}[{index}]"] = str(item)
        elif isinstance(value, dict):
            flat.update(_flatten_params(value, full_key))
        else:
            flat[full_key] = str(value)
    return flat
