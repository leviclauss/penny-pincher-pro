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

    # "alpaca" or "polygon". Polygon is preferred (returns OI + volume); the
    # pipeline falls back to Alpaca when polygon is selected but no key set.
    options_provider: str = "alpaca"
    polygon_api_key: str = Field(default="")
    polygon_base_url: str = "https://api.polygon.io"
    # Developer tier is effectively unlimited; Starter is also unlimited but
    # EOD-only. The limiter is a defensive cap, not a budget — bump only if
    # you observe 429s.
    polygon_rate_limit_per_min: int = 100

    polygon_flatfiles_access_key_id: str = Field(default="")
    polygon_flatfiles_secret_access_key: str = Field(default="")
    polygon_flatfiles_s3_region: str = "us-east-1"

    risk_free_rate: float = 0.045
    options_max_dte: int = 60
    options_strike_pct_window: float = 0.15

    finnhub_api_key: str = Field(default="")
    finnhub_base_url: str = "https://finnhub.io/api/v1"
    earnings_lookahead_days: int = 90
    # Free tier ceiling is 60 cpm; default leaves a small safety margin.
    finnhub_rate_limit_per_min: int = 55
    # Bulk earnings fetch (1 req/run + per-symbol fallback for missing) is
    # OFF by default: Finnhub's bulk calendar endpoint silently omits some
    # upcoming reports (observed: MSTR's near-term report missing from bulk
    # while the per-symbol query returned it correctly). The rate limiter
    # above already keeps per-symbol within the free-tier budget, so the
    # safer default is per-symbol everywhere. Flip to True only if you've
    # validated bulk against per-symbol for your universe and accept the
    # risk of an occasionally-wrong "next earnings" date. See
    # docs/ops/api-rate-limits.md.
    finnhub_earnings_use_bulk: bool = False

    yahoo_base_url: str = "https://query1.finance.yahoo.com"
    macro_lookback_days: int = 365
    spy_symbol: str = "SPY"

    scheduler_enabled: bool = True
    scheduler_evening_hour: int = 17
    scheduler_evening_minute: int = 30
    scheduler_screener_offset_minutes: int = 30
    scheduler_universe_scan_offset_minutes: int = 60
    # Options-history keep-current backfills *yesterday's* flat-file chain
    # rows so the strategy backtest's RealChainPricer keeps rolling forward.
    # Defaults to 90 minutes after the evening pipeline so flat files have
    # settled. Skips cleanly when Polygon flat-file creds aren't configured.
    scheduler_options_history_offset_minutes: int = 90
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
    # (both go through the boto3 S3 client; B2 just needs its S3-compatible
    # endpoint URL set). Requires the optional ``backup-s3`` extra:
    # ``pip install -e .[backup-s3]``.
    backup_offsite_enabled: bool = False
    backup_offsite_provider: str = Field(default="")
    backup_offsite_bucket: str = Field(default="")
    backup_offsite_prefix: str = Field(default="")
    # Custom endpoint (required for B2, optional for AWS). For Backblaze the
    # value looks like https://s3.us-west-002.backblazeb2.com.
    backup_offsite_endpoint_url: str = Field(default="")
    backup_offsite_region: str = Field(default="")
    backup_offsite_access_key_id: str = Field(default="")
    backup_offsite_secret_access_key: str = Field(default="")

    # --- Healthchecks.io heartbeat ---
    # Per-job ping URL is read from env var HEALTHCHECKS_URL_<JOB_NAME> (see
    # core.healthchecks). This master switch turns the whole feature off
    # without unsetting individual env vars.
    healthchecks_enabled: bool = True
    healthchecks_timeout_s: float = 5.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
