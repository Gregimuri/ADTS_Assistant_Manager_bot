from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str
    spreadsheet_id: str = "1sRy5VuFGEWsh_RZCGUkWKyKcLP8i6dO-F1smi4ok4Mk"
    spreadsheet_gid: int = 0
    sheets_cache_ttl_seconds: int = 600
    price_base: int = 1000
    price_per_unit: int = 500


@lru_cache
def get_settings() -> Settings:
    return Settings()
