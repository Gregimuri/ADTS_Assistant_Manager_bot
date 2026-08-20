from __future__ import annotations

import html
import re

from app.config import Settings
from app.services.catalog import StoreMatch, ToMatch

EMM_TAG_RE = re.compile(r"#счетемм", re.IGNORECASE)
TO_TAG_RE = re.compile(r"#счетто", re.IGNORECASE)
ANY_INVOICE_TAG_RE = re.compile(r"#счет(?:емм|то)", re.IGNORECASE)
TELEGRAM_MESSAGE_LIMIT = 4096


def parse_store_names(text: str, tag_re: re.Pattern[str] | None = None) -> list[str]:
    pattern = tag_re or ANY_INVOICE_TAG_RE
    names: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        without_tag = pattern.sub("", line).strip(" \t-—:")
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
    blocks = [_format_emm_store_block(match, settings) for match in matches]
    total = sum(store_price(match, settings) for match in matches)
    return "\n\n".join(blocks), total


def build_to_invoice_reply(
    query: str,
    matches: list[ToMatch],
    settings: Settings,
) -> tuple[str, int]:
    if not matches:
        return f"{_escape(query)}: ТТ не найдена", 0
    blocks = [_format_to_store_block(match, settings) for match in matches]
    total = sum(to_store_price(match) for match in matches)
    return "\n\n".join(blocks), total


def store_price(match: StoreMatch, settings: Settings) -> int:
    units = match.emm_count + match.flash_count + match.cube_count
    if units <= 0:
        return 0
    return settings.price_base + settings.price_per_unit * units


def to_store_price(match: ToMatch) -> int:
    return match.visit.actual_cost + match.visit.extra_cost


def format_total_line(total: int) -> str:
    return _bold(f"Итого сумма затрат составляет: {total} руб.")


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


def bitrix_task_url(task_id: str, settings: Settings) -> str:
    task_id = task_id.strip()
    if not task_id:
        return ""
    if task_id.startswith("http://") or task_id.startswith("https://"):
        return task_id
    return settings.bitrix_task_url_template.format(task_id=task_id)


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _bold(text: str) -> str:
    return f"<b>{_escape(text)}</b>"


def _italic(text: str) -> str:
    return f"<i>{_escape(text)}</i>"


def _format_emm_store_block(match: StoreMatch, settings: Settings) -> str:
    address = clean_address(match.address)
    store_name = _escape(match.query)
    header = f"<b>{store_name}</b>, {_escape(address)}" if address else f"<b>{store_name}</b>"
    work = _work_details(match.emm_count, match.flash_count, match.cube_count)
    price = store_price(match, settings)
    if work:
        work_line = f"          — Выезд на ТО ({_italic(work)}) - {price}"
    else:
        work_line = "          — Работы по ЕММ не найдены"
    return (
        f"{header}\n"
        f"{_italic('Ссылка на задачу -')} \n"
        "Фото акта приложено.\n"
        f"{work_line}"
    )


def _format_to_store_block(match: ToMatch, settings: Settings) -> str:
    visit = match.visit
    address = clean_address(visit.address)
    store_name = _escape(match.query)
    header = f"<b>{store_name}</b>, {_escape(address)}" if address else f"<b>{store_name}</b>"

    task_url = bitrix_task_url(visit.bitrix_task_id, settings)
    if task_url:
        task_line = f"{_italic('Номер задачи -')} <a href=\"{_escape(task_url)}\">{_escape(task_url)}</a>"
    else:
        task_line = f"{_italic('Номер задачи -')} -"

    work_label = visit.work_type.strip() if visit.work_type.strip() else "-"
    tt_total = to_store_price(match)
    return (
        f"{header}\n"
        f"{task_line}\n"
        "Фото акта приложено.\n"
        f"          — Выезд на ТО ({_italic(work_label)}) - {visit.actual_cost} р\n"
        f"          — Доп затраты - {visit.extra_cost} р\n"
        f"          {_bold(f'Итого сумма затрат по ТТ составляет: {tt_total} руб.')}"
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
