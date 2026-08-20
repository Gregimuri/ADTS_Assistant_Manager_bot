from __future__ import annotations

import html
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
) -> tuple[str, int]:
    if not matches:
        return f"{_escape(query)}: ТТ не найдена", 0
    blocks = [_format_store_block(match, settings) for match in matches]
    total = sum(store_price(match, settings) for match in matches)
    return "\n\n".join(blocks), total


def store_price(match: StoreMatch, settings: Settings) -> int:
    units = match.emm_count + match.flash_count + match.cube_count
    if units <= 0:
        return 0
    return settings.price_base + settings.price_per_unit * units


def format_total_line(total: int) -> str:
    return f"<b>Итого сумма затрат составляет: {total} руб.</b>"


def join_invoice_blocks(blocks: list[str], total: int | None = None) -> list[str]:
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

    if total is None or not messages:
        return messages

    total_line = format_total_line(total)
    last = messages[-1]
    candidate = f"{last}\n\n{total_line}"
    if len(candidate) <= TELEGRAM_MESSAGE_LIMIT:
        messages[-1] = candidate
    else:
        messages.append(total_line)
    return messages


def clean_address(address: str) -> str:
    parts = [part.strip() for part in address.split(",")]
    return ", ".join(part for part in parts if part)


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _bold_italic(text: str) -> str:
    return f"<b><i>{_escape(text)}</i></b>"


def _format_store_block(match: StoreMatch, settings: Settings) -> str:
    address = clean_address(match.address)
    store_name = _escape(match.query)
    header = f"<b>{store_name}</b>, {_escape(address)}" if address else f"<b>{store_name}</b>"
    work = _work_details(match.emm_count, match.flash_count, match.cube_count)
    price = store_price(match, settings)
    if work:
        work_line = f"          — Выезд на ТО ({_bold_italic(work)}) - {price}"
    else:
        work_line = "          — Работы по ЕММ не найдены"
    return (
        f"{header}\n"
        f"{_bold_italic('Ссылка на задачу -')} \n"
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
