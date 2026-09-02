from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

PROJECT_EXIT_DATE_COLUMNS: dict[str, tuple[str, ...]] = {
    "ШБ": (
        "Дата обследования",
        "Дата СКС",
        "Дата монтажа экранов",
    ),
    "ММ": (
        "Дата старта монтажа",
        "Дата выхода на объект (повторный)",
    ),
    "МА": (
        "Дата СМР",
        "Дата повторного выхода",
    ),
    "Лента": (
        "Дата Монтажа",
        "Дата повторного монтажа",
    ),
    "Фасоль": (
        "Дата монтажа",
        "Дата повторного монтажа",
    ),
    "Метро": (
        "Дата Монтажа",
        "Дата повторного монтажа",
    ),
    "ФЭ": (
        "Дата СКС",
        "Дата выхода на объект",
        "Дата выхода на объект (повторный)",
    ),
    "ДО": (
        "Дата Монтажа",
        "Дата повторного монтажа",
    ),
    "ТО": (
        "Дата сервисного выезда",
        "Дата повторного выезда",
    ),
}

_CONFIG_BY_PROJECT = {name.casefold(): columns for name, columns in PROJECT_EXIT_DATE_COLUMNS.items()}


def normalize_header(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").split())


_EXIT_DATE_SKIP_HEADERS = frozenset(
    {
        "дата заказа",
        "дата создания",
        "дата добавления",
        "дата изменения",
        "дата подачи света",
        "дата в таблице магнит",
        "дата отправки фо",
    }
)
_EXIT_DATE_KEYWORDS = (
    "выход",
    "скс",
    "обслед",
    "сервис",
    "монтаж",
    "выезд",
    "повтор",
    "смр",
)


def resolve_exit_date_headers(project: str, headers: list[str]) -> list[str]:
    configured = _CONFIG_BY_PROJECT.get(project.casefold(), ())
    by_normalized = {normalize_header(header): header for header in headers if header}
    resolved: list[str] = []
    for column in configured:
        match = by_normalized.get(normalize_header(column))
        if match:
            resolved.append(match)
            continue
        logger.warning(
            "Exit plan column %r not found on project sheet %s",
            column,
            project,
        )
    if resolved:
        return resolved
    return _fallback_exit_date_headers(headers)


def _fallback_exit_date_headers(headers: list[str]) -> list[str]:
    result: list[str] = []
    for header in headers:
        if not header:
            continue
        norm = normalize_header(header)
        if "дата" not in norm:
            continue
        if norm in _EXIT_DATE_SKIP_HEADERS:
            continue
        if any(keyword in norm for keyword in _EXIT_DATE_KEYWORDS):
            result.append(header)
    return result
