"""Typed application settings sourced from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "dev"
    timezone: str = "America/Los_Angeles"
    log_level: str = "INFO"
    log_json: bool = False

    database_url: str = f"sqlite:///{REPO_ROOT / 'data' / 'wheel.db'}"

    alpaca_api_key: str = Field(default="")
    alpaca_api_secret: str = Field(default="")
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_feed: str = "iex"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
