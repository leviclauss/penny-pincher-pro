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
    # Free tier ceiling is 60 cpm; default leaves a small safety margin.
    finnhub_rate_limit_per_min: int = 55
    # When True, fetch earnings via the bulk calendar endpoint (1 req/run)
    # and fall back to per-symbol queries only for active symbols missing
    # from the bulk payload. See backend/docs/api-rate-limits.md.
    finnhub_earnings_use_bulk: bool = True

    yahoo_base_url: str = "https://query1.finance.yahoo.com"
    macro_lookback_days: int = 365
    spy_symbol: str = "SPY"

    scheduler_enabled: bool = True
    scheduler_evening_hour: int = 17
    scheduler_evening_minute: int = 30
    scheduler_screener_offset_minutes: int = 30
    scheduler_universe_scan_offset_minutes: int = 60
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

    # Inbound bot (long-poll). Off by default — flip on per deployment once
    # the outbound side is configured. Long-poll timeout is the Bot API
    # ``getUpdates`` timeout; the HTTP read timeout is timeout + a small
    # buffer so the request can return cleanly when no updates are pending.
    telegram_inbound_enabled: bool = False
    telegram_inbound_long_poll_s: int = 25
    telegram_inbound_heartbeat_s: int = 300
    telegram_inbound_idle_sleep_s: float = 1.0
    telegram_inbound_max_failures: int = 5

    # --- Email (SMTP) channel ---
    smtp_host: str = Field(default="")
    smtp_port: int = 587
    smtp_user: str = Field(default="")
    smtp_password: str = Field(default="")
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    smtp_from_address: str = Field(default="")
    smtp_to_address: str = Field(default="")
    smtp_timeout_s: float = 10.0

    # --- ntfy.sh push channel ---
    ntfy_server_url: str = "https://ntfy.sh"
    ntfy_topic: str = Field(default="")
    ntfy_token: str = Field(default="")
    ntfy_priority: str = "default"
    ntfy_timeout_s: float = 10.0

    # Base URL of the local web UI (e.g. Tailscale hostname). When set,
    # alert templates render "Open" deep links into the relevant pages.
    web_base_url: str = Field(default="")

    # --- Nightly SQLite backup ---
    # Local snapshot directory. Relative paths resolve next to the live DB
    # file (so the default lands in repo_root/data/backups/).
    backup_dir: str = str(REPO_ROOT / "data" / "backups")
    backup_retention: int = 14
    scheduler_backup_hour: int = 3
    scheduler_backup_minute: int = 0
    # Off-site upload — disabled by default. Provider is one of "s3" or "b2"
    # (see scheduler.jobs.backup.upload_offsite for the current stub +
    # wiring note).
    backup_offsite_enabled: bool = False
    backup_offsite_provider: str = Field(default="")
    backup_offsite_bucket: str = Field(default="")
    backup_offsite_prefix: str = Field(default="")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
