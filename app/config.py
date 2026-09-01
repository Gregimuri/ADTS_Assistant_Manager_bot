from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    admin_user_ids: frozenset[int] = frozenset({1029160022})
    do_report_user_ids: frozenset[int] = frozenset({1029160022})
    bitrix_webhook_url: str = "https://adts.bitrix24.ru/rest/227/9hhckruwy6wbutw6/"
    bitrix_assembly_responsible_id: int = 197
    bitrix_assembly_creator_id: int = 439
    report_data_path: str = "data/reports.json"
    do_order_horizon_days: int = 17
    do_moscow_order_horizon_days: int = 2
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

    @property
    def public_base_url(self) -> str:
        return self.webhook_base_url.rstrip("/")

    @field_validator("admin_user_ids", "do_report_user_ids", mode="before")
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
