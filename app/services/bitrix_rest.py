from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

import aiohttp

from app.config import Settings

_TASK_VIEW_RE = re.compile(r"task/view/(\d+)", re.IGNORECASE)


def parse_bitrix_task_id(value: str) -> str:
    """ID задачи или URL Bitrix → числовой id.

    Не выдирает цифры из дат/телефонов — только чистый id или /task/view/<id>/.
    """
    text = (value or "").strip()
    if not text:
        return ""
    if text.isdigit():
        return text
    match = _TASK_VIEW_RE.search(text)
    if match:
        return match.group(1)
    return ""


def flatten_params(params: dict[str, Any], prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in params.items():
        full_key = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, list):
            for index, item in enumerate(value):
                flat[f"{full_key}[{index}]"] = str(item)
        elif isinstance(value, dict):
            flat.update(flatten_params(value, full_key))
        else:
            flat[full_key] = str(value)
    return flat


async def bitrix_call(
    settings: Settings,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    timeout_seconds: int = 120,
    json_body: bool = False,
) -> Any:
    base = settings.bitrix_webhook_url.rstrip("/") + "/"
    if not settings.bitrix_webhook_url.strip():
        raise RuntimeError("BITRIX_WEBHOOK_URL не задан.")
    url = urljoin(base, method)
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if json_body:
            request = session.post(url, json=params or {})
        else:
            request = session.post(url, data=flatten_params(params or {}))
        async with request as response:
            body = await response.text()
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Bitrix вернул не-JSON ответ (HTTP {response.status})."
                ) from exc
            if response.status >= 400 and not (
                isinstance(data, dict) and data.get("error")
            ):
                raise RuntimeError(f"Bitrix HTTP {response.status}: {body[:300]}")
    if not isinstance(data, dict):
        raise RuntimeError("Bitrix вернул неожиданный ответ.")
    if data.get("error"):
        code = str(data.get("error") or "")
        description = str(data.get("error_description") or code)
        if code and code not in description:
            description = f"{description} ({code})"
        raise RuntimeError(f"Bitrix API: {description}")
    return data.get("result")
