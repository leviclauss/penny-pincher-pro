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
    alpaca_options_feed: str = "indicative"

    risk_free_rate: float = 0.045
    options_max_dte: int = 60
    options_strike_pct_window: float = 0.15

    finnhub_api_key: str = Field(default="")
    finnhub_base_url: str = "https://finnhub.io/api/v1"
    earnings_lookahead_days: int = 90

    yahoo_base_url: str = "https://query1.finance.yahoo.com"
    macro_lookback_days: int = 365
    spy_symbol: str = "SPY"

    scheduler_enabled: bool = True
    scheduler_evening_hour: int = 17
    scheduler_evening_minute: int = 30
    scheduler_screener_offset_minutes: int = 30
    scheduler_positions_hour: int = 18
    scheduler_positions_minute: int = 0
    scheduler_morning_digest_hour: int = 8
    scheduler_morning_digest_minute: int = 0
    scheduler_evening_digest_hour: int = 18
    scheduler_evening_digest_minute: int = 30
    market_calendar: str = "NYSE"

    # Intraday alert pulse — disabled by default; opt in per deployment.
    scheduler_intraday_enabled: bool = False
    scheduler_intraday_interval_minutes: int = 15
    intraday_quote_max_age_s: int = 90
    intraday_iv_spike_enabled: bool = False
    intraday_iv_spike_pct: float = 0.20
    intraday_iv_spike_interval_minutes: int = 30

    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")
    telegram_parse_mode: str = "MarkdownV2"
    telegram_disable_preview: bool = True
    telegram_timeout_s: float = 10.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
