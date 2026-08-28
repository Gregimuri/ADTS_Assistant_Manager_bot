from __future__ import annotations

import html
import re

from app.config import Settings
from app.services.catalog import DoReportMatch, InfoMatch, StoreMatch, ToMatch

EMM_TAG_RE = re.compile(r"#счетемм", re.IGNORECASE)
TO_TAG_RE = re.compile(r"#счетто", re.IGNORECASE)
INFO_TAG_RE = re.compile(r"#инфотт", re.IGNORECASE)
DO_TAG_RE = re.compile(r"#до(?!\w)", re.IGNORECASE)
ANY_INVOICE_TAG_RE = re.compile(r"#(?:счет(?:емм|то)|инфотт|до(?!\w))", re.IGNORECASE)
TELEGRAM_MESSAGE_LIMIT = 4096
DO_EMPTY_REPORT_MESSAGE = "на ближайшие 17 дней расходка везде отправлена"


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


def build_info_reply(query: str, matches: list[InfoMatch]) -> str:
    if not matches:
        return f"{_escape(query)}: ТТ не найдена"
    return "\n".join(_format_info_block(match) for match in matches)


def build_do_report_blocks(matches: list[DoReportMatch]) -> list[str]:
    if not matches:
        return [DO_EMPTY_REPORT_MESSAGE]

    grouped: dict[str, list[DoReportMatch]] = {}
    manager_order: list[str] = []
    for match in matches:
        manager = match.manager.strip() or "—"
        if manager not in grouped:
            grouped[manager] = []
            manager_order.append(manager)
        grouped[manager].append(match)

    lines = [_bold("Отсутствует расходка по ДО:"), ""]
    for manager in manager_order:
        stores = grouped[manager]
        lines.append(f"{_underline(manager)} - {_bold(f'{len(stores)} ТТ')}")
        for index, store in enumerate(stores, start=1):
            region = store.region.strip() or "-"
            address = clean_address(store.address) or "-"
            order_date = store.order_date.strip() or "-"
            lines.append(
                f"{index}) {_escape(store.name)} - {_escape(region)} - "
                f"{_escape(address)} - {_bold(order_date)}"
            )
        lines.append("")

    body = "\n".join(lines).rstrip()
    return _split_long_message(body)


def _split_long_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}".strip("\n") if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(line) <= limit:
            current = line
            continue
        start = 0
        while start < len(line):
            chunks.append(line[start : start + limit])
            start += limit
        current = ""
    if current:
        chunks.append(current)
    return chunks


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


def _underline(text: str) -> str:
    return f"<u>{_escape(text)}</u>"


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

    task_url = bitrix_task_url(match.bitrix_task_id, settings)
    if task_url:
        task_line = f"{_italic('Ссылка на задачу -')} <a href=\"{_escape(task_url)}\">{_escape(task_url)}</a>"
    else:
        task_line = f"{_italic('Ссылка на задачу -')} "

    return (
        f"{header}\n"
        f"{task_line}\n"
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
        task_line = f"{_italic('Ссылка на задачу -')} <a href=\"{_escape(task_url)}\">{_escape(task_url)}</a>"
    else:
        task_line = f"{_italic('Ссылка на задачу -')} -"

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


def _format_info_block(match: InfoMatch) -> str:
    title = f"{match.project} {match.name}".strip()
    region = match.region.strip() or "-"
    address = clean_address(match.address) or "-"
    manager = match.manager.strip() or "-"
    return " - ".join(
        [
            _bold(title),
            _escape(region),
            _escape(address),
            _escape(manager),
        ]
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
