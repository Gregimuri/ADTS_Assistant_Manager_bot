from functools import lru_cache

from pydantic import AliasChoices, Field
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
