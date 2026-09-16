from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin

import aiohttp

from app.config import Settings
from app.services.bitrix_rest import bitrix_call, flatten_params
from app.services.dates import msk_today, parse_ru_date

logger = logging.getLogger(__name__)

_PAGE_SIZE = 50


@dataclass(frozen=True, slots=True)
class BitrixTask:
    task_id: str
    title: str
    description: str
    description_bbcode: str
    status: int
    real_status: int
    created_date: date | None
    closed_date: date | None
    responsible_id: int
    creator_id: int


class BitrixTasksError(RuntimeError):
    """Не удалось загрузить задачи из Bitrix."""


class BitrixTasksClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.bitrix_webhook_url.rstrip("/") + "/"
        self._store_task_cache: dict[tuple[str, bool], str] = {}

    async def list_assembly_tasks(self) -> list[BitrixTask]:
        if not self._settings.bitrix_webhook_url.strip():
            raise BitrixTasksError("BITRIX_WEBHOOK_URL не задан.")

        base_filter = {
            "RESPONSIBLE_ID": self._settings.bitrix_assembly_responsible_id,
            "CREATED_BY": self._settings.bitrix_assembly_creator_id,
        }
        today_start = _bitrix_day_start(msk_today())
        fetch_filters = [
            {**base_filter, "!REAL_STATUS": 5},
            {**base_filter, ">CREATED_DATE": today_start},
            {**base_filter, "REAL_STATUS": 5, ">CLOSED_DATE": today_start},
        ]

        tasks_by_id: dict[str, BitrixTask] = {}
        for extra_filter in fetch_filters:
            for task in await self._fetch_tasks(extra_filter):
                if _matches_assembly_task(task, self._settings):
                    tasks_by_id[task.task_id] = task

        matched = list(tasks_by_id.values())
        logger.info("Loaded %s assembly tasks from Bitrix", len(matched))
        return matched

    async def find_task_id_for_store(self, query: str, *, emm_only: bool = False) -> str:
        """Ищет задачу Bitrix по названию ТТ (лист ТО больше не хранит id)."""
        key = (query.strip().casefold(), emm_only)
        if key in self._store_task_cache:
            return self._store_task_cache[key]
        cleaned = query.strip()
        if not cleaned or not self._settings.bitrix_webhook_url.strip():
            self._store_task_cache[key] = ""
            return ""

        search_terms: list[str] = [cleaned]
        parts = cleaned.split()
        if len(parts) > 2:
            search_terms.append(" ".join(parts[:2]))
        if parts:
            search_terms.append(parts[0])

        seen_terms: set[str] = set()
        task_id = ""
        for term in search_terms:
            marker = term.casefold()
            if marker in seen_terms:
                continue
            seen_terms.add(marker)
            try:
                raw_tasks = await self._search_tasks_by_title(term)
            except BitrixTasksError:
                logger.exception("Bitrix task lookup failed for %r", term)
                break
            picked = _pick_store_task(raw_tasks, cleaned, emm_only=emm_only)
            if picked:
                task_id = picked
                break

        self._store_task_cache[key] = task_id
        return task_id

    async def _search_tasks_by_title(self, term: str) -> list[dict[str, Any]]:
        try:
            payload = await bitrix_call(
                self._settings,
                "tasks.task.list",
                {
                    "select": ["ID", "TITLE", "DESCRIPTION"],
                    "filter": {"%TITLE": term},
                    "start": 0,
                },
                timeout_seconds=60,
            )
        except RuntimeError as exc:
            raise BitrixTasksError(str(exc)) from exc
        if isinstance(payload, dict):
            batch = payload.get("tasks") or []
            return [item for item in batch if isinstance(item, dict)]
        return []

    async def _fetch_tasks(self, filter_params: dict[str, Any]) -> list[BitrixTask]:
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
                            "DESCRIPTION_IN_BBCODE",
                            "STATUS",
                            "REAL_STATUS",
                            "CREATED_DATE",
                            "CLOSED_DATE",
                            "RESPONSIBLE_ID",
                            "CREATED_BY",
                        ],
                        "filter": filter_params,
                        "start": start,
                    },
                )
                batch = payload.get("tasks") or []
                for item in batch:
                    parsed = _parse_task(item)
                    if parsed is not None:
                        tasks.append(parsed)

                next_start = payload.get("next")
                if next_start is not None:
                    start = int(next_start)
                    continue
                if len(batch) < _PAGE_SIZE:
                    break
                start += _PAGE_SIZE
        return tasks

    async def _call(
        self,
        session: aiohttp.ClientSession,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        url = urljoin(self._base_url, method)
        async with session.get(url, params=flatten_params(params)) as response:
            response.raise_for_status()
            data = await response.json(content_type=None)
        if not isinstance(data, dict):
            raise BitrixTasksError("Bitrix вернул неожиданный ответ.")
        if data.get("error"):
            raise BitrixTasksError(f"Bitrix API: {data.get('error_description') or data['error']}")
        result = data.get("result")
        if isinstance(result, list):
            return {"tasks": result}
        if not isinstance(result, dict):
            raise BitrixTasksError("Bitrix API: пустой result.")
        return result


def _pick_store_task(
    raw_tasks: list[dict[str, Any]],
    store_query: str,
    *,
    emm_only: bool,
) -> str:
    for raw in raw_tasks:
        title = str(raw.get("title") or raw.get("TITLE") or "")
        description = str(raw.get("description") or raw.get("DESCRIPTION") or "")
        if emm_only and "емм" not in f"{title} {description}".casefold():
            continue
        if _store_name_in_title(title, store_query):
            return str(raw.get("id") or raw.get("ID") or "").strip()
    for raw in raw_tasks:
        title = str(raw.get("title") or raw.get("TITLE") or "")
        description = str(raw.get("description") or raw.get("DESCRIPTION") or "")
        if emm_only and "емм" not in f"{title} {description}".casefold():
            continue
        if store_query.casefold() in title.casefold():
            return str(raw.get("id") or raw.get("ID") or "").strip()
    return ""


def _store_name_in_title(title: str, store_query: str) -> bool:
    pattern = rf"(?<!\w){re.escape(store_query)}(?!\w)"
    return bool(re.search(pattern, title, flags=re.IGNORECASE))


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
    if "сборка" not in _task_text(task):
        return False
    return (
        task.responsible_id == settings.bitrix_assembly_responsible_id
        and task.creator_id == settings.bitrix_assembly_creator_id
    )


def _task_text(task: BitrixTask) -> str:
    return f"{task.title} {task.description} {task.description_bbcode}".casefold()


def _is_open(task: BitrixTask) -> bool:
    status = task.real_status or task.status
    return status != 5


def _parse_task(raw: dict[str, Any]) -> BitrixTask | None:
    task_id = str(raw.get("id") or raw.get("ID") or "").strip()
    if not task_id:
        return None
    status = int(raw.get("status") or raw.get("STATUS") or 0)
    real_status = int(
        raw.get("realStatus")
        or raw.get("REAL_STATUS")
        or raw.get("subStatus")
        or raw.get("SUB_STATUS")
        or status
        or 0
    )
    responsible_id = _parse_user_id(
        raw.get("responsibleId")
        or raw.get("RESPONSIBLE_ID")
        or _nested_user_id(raw.get("responsible") or raw.get("RESPONSIBLE"))
    )
    creator_id = _parse_user_id(
        raw.get("createdBy")
        or raw.get("CREATED_BY")
        or _nested_user_id(raw.get("creator") or raw.get("CREATOR"))
    )
    return BitrixTask(
        task_id=task_id,
        title=str(raw.get("title") or raw.get("TITLE") or ""),
        description=str(raw.get("description") or raw.get("DESCRIPTION") or ""),
        description_bbcode=str(
            raw.get("descriptionInBbcode") or raw.get("DESCRIPTION_IN_BBCODE") or ""
        ),
        status=status,
        real_status=real_status,
        created_date=_parse_bitrix_date(raw.get("createdDate") or raw.get("CREATED_DATE")),
        closed_date=_parse_bitrix_date(raw.get("closedDate") or raw.get("CLOSED_DATE")),
        responsible_id=responsible_id,
        creator_id=creator_id,
    )


def _parse_user_id(value: object) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _nested_user_id(value: object) -> object:
    if isinstance(value, dict):
        return value.get("id") or value.get("ID")
    return value


def _bitrix_day_start(day: date) -> str:
    return f"{day.isoformat()}T00:00:00+03:00"


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
