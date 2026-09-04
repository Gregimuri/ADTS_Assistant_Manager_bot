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


def resolve_exit_date_headers(project: str, headers: list[str]) -> list[str]:
    """Только колонки из PROJECT_EXIT_DATE_COLUMNS — без keyword-fallback."""
    configured = _CONFIG_BY_PROJECT.get(project.casefold(), ())
    if not configured:
        logger.warning("Exit date columns are not configured for project %s", project)
        return []

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
    return resolved
