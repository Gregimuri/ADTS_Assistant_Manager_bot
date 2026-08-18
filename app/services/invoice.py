from __future__ import annotations

import re

from app.config import Settings
from app.services.catalog import StoreMatch

TAG_RE = re.compile(r"#счетемм", re.IGNORECASE)
TELEGRAM_MESSAGE_LIMIT = 4096


def parse_store_names(text: str) -> list[str]:
    names: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        without_tag = TAG_RE.sub("", line).strip(" \t-—:")
        if not without_tag:
            continue
        names.append(without_tag)
    return names


def build_invoice_reply(
    query: str,
    matches: list[StoreMatch],
    settings: Settings,
) -> str:
    if not matches:
        return f"{query}: ТТ не найдена"
    return "\n\n".join(_format_store_block(match, settings) for match in matches)


def join_invoice_blocks(blocks: list[str]) -> list[str]:
    """Собирает блоки в сообщения, не превышая лимит Telegram."""
    messages: list[str] = []
    current = ""
    for block in blocks:
        if not current:
            current = block
            continue
        candidate = f"{current}\n\n{block}"
        if len(candidate) <= TELEGRAM_MESSAGE_LIMIT:
            current = candidate
        else:
            messages.append(current)
            current = block
    if current:
        messages.append(current)
    return messages


def clean_address(address: str) -> str:
    parts = [part.strip() for part in address.split(",")]
    return ", ".join(part for part in parts if part)


def _format_store_block(match: StoreMatch, settings: Settings) -> str:
    address = clean_address(match.address)
    header = f"{match.query}, {address}" if address else match.query
    work = _work_details(match.emm_count, match.flash_count, match.cube_count)
    price = settings.price_base + settings.price_per_unit * (
        match.emm_count + match.flash_count + match.cube_count
    )
    if work:
        work_line = f"          — Выезд на ТО ({work}) - {price}"
    else:
        work_line = "          — Работы по ЕММ не найдены"
    return (
        f"{header}\n"
        "Ссылка на задачу - \n"
        "Фото акта приложено.\n"
        f"{work_line}"
    )


def _work_details(emm_count: int, flash_count: int, cube_count: int) -> str:
    parts: list[str] = []
    if emm_count:
        parts.append(f"ЕММ на {emm_count} {ru_plural(emm_count, 'устройство', 'устройства', 'устройств')}")
    if flash_count:
        parts.append(f"{flash_count} {ru_plural(flash_count, 'прошивка', 'прошивки', 'прошивок')}")
    if cube_count:
        device_word = ru_plural(cube_count, "устройство", "устройства", "устройств")
        parts.append(f"установка кубика на {cube_count} {device_word}")
    return ", ".join(parts)


def ru_plural(n: int, one: str, few: str, many: str) -> str:
    n_abs = abs(n) % 100
    if 11 <= n_abs <= 19:
        return many
    last = n_abs % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many
