from functools import lru_cache
from typing import Annotated

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# CSV в .env (201,203,641) — не JSON; без NoDecode pydantic-settings падает на старте.
_CsvIntSet = Annotated[frozenset[int], NoDecode]
_CsvIntTuple = Annotated[tuple[int, ...], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    bot_token: str
    spreadsheet_id: str = "1sRy5VuFGEWsh_RZCGUkWKyKcLP8i6dO-F1smi4ok4Mk"
    spreadsheet_gid: int = 0
    to_sheet_name: str = "ТО"
    directory_sheet_name: str = "Справочник"
    do_sheet_name: str = "ДО"
    do_report_chat_id: int = -4893962129
    admin_report_chat_id: int = -5278414891
    admin_user_ids: _CsvIntSet = frozenset({1029160022})
    do_report_user_ids: _CsvIntSet = frozenset({1029160022})
    bitrix_webhook_url: str = "https://adts.bitrix24.ru/rest/401/t83wze013cj1wvky/"
    bitrix_assembly_responsible_id: int = 197
    bitrix_assembly_creator_id: int = 439
    bitrix_fo_responsible_ids: _CsvIntTuple = Field(
        default=(201, 203, 641),
        validation_alias=AliasChoices(
            "BITRIX_FO_RESPONSIBLE_IDS",
            "bitrix_fo_responsible_ids",
            "BITRIX_FO_RESPONSIBLE_ID",
            "bitrix_fo_responsible_id",
        ),
    )
    bitrix_fo_fallback_creator_id: int = 401
    bitrix_fo_auditor_ids: _CsvIntSet = frozenset({281, 203, 201, 401, 641})
    report_data_path: str = "data/reports.json"
    scheduled_exit_projects: str = "ДО,ШБ,ММ,МА,Лента,Фасоль,Метро,ФЭ,ТО"
    do_order_horizon_days: int = 17
    do_moscow_order_horizon_days: int = 17
    sheets_cache_ttl_seconds: int = 600
    price_base: int = 1000
    price_per_unit: int = 500
    bitrix_task_url_template: str = (
        "https://adts.bitrix24.ru/company/personal/user/189/tasks/task/view/{task_id}/"
    )
    port: int | None = Field(default=None, validation_alias="PORT")
    webhook_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("WEBHOOK_BASE_URL", "RENDER_EXTERNAL_URL"),
    )
    telegram_local_api_url: str = Field(
        default="",
        validation_alias=AliasChoices("TELEGRAM_LOCAL_API_URL", "BOT_API_URL"),
    )

    @property
    def public_base_url(self) -> str:
        return self.webhook_base_url.rstrip("/")

    @field_validator(
        "admin_user_ids",
        "do_report_user_ids",
        "bitrix_fo_auditor_ids",
        mode="before",
    )
    @classmethod
    def _parse_user_ids(cls, value: object) -> frozenset[int]:
        if value is None or value == "":
            return frozenset()
        if isinstance(value, frozenset):
            return value
        if isinstance(value, int):
            return frozenset({value})
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",") if part.strip()]
            return frozenset(int(part) for part in parts)
        if isinstance(value, (list, tuple, set)):
            return frozenset(int(item) for item in value)
        raise ValueError(f"Invalid user id list: {value!r}")

    @field_validator("bitrix_fo_responsible_ids", mode="before")
    @classmethod
    def _parse_responsible_ids(cls, value: object) -> tuple[int, ...]:
        if value is None or value == "":
            return (201, 203, 641)
        if isinstance(value, tuple) and value and all(isinstance(i, int) for i in value):
            return value
        if isinstance(value, int):
            return (value,)
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",") if part.strip()]
            return tuple(int(part) for part in parts) or (201, 203, 641)
        if isinstance(value, (list, tuple)):
            parsed = tuple(int(item) for item in value)
            return parsed or (201, 203, 641)
        raise ValueError(f"Invalid responsible id list: {value!r}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
