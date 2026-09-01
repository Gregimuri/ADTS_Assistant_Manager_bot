from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

_MSK = timezone(timedelta(hours=3))
_DATE_FORMATS = ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%y")


def msk_today() -> date:
    return datetime.now(_MSK).date()


def msk_yesterday() -> date:
    return msk_today() - timedelta(days=1)


def parse_ru_date(value: str) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def format_ru_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")
