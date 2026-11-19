"""Environment-backed settings (pydantic-settings, .env supported)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = ""
    affiliate_hepsiburada: str = ""
    affiliate_trendyol: str = ""
    affiliate_n11: str = ""
    affiliate_amazon_tag: str = ""
    ucuzu_db_path: str = "ucuzu.db"
    fetch_proxy_url: str = ""
    exa_api_key: str = ""


settings = Settings()