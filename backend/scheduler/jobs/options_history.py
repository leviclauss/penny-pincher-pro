"""Daily ``options_historical`` keep-current job.

Pulls *yesterday's* option chain via Polygon S3 flat files for every active
ticker so the backtest's ``RealChainPricer`` keeps rolling forward without
manual cron. Flat files settle the previous trading day overnight, so the
target window is always [yesterday, yesterday] in market time.

Skip branches (each lands a row in ``job_runs`` so the cause is visible):
- ``skipped="holiday"`` — ``yesterday`` was an NYSE-closed day.
- ``skipped="no_credentials"`` — ``POLYGON_FLATFILES_*`` not configured.
- ``skipped="no_active_tickers"`` — empty watchlist.
- ``skipped="boto3_missing"`` — the ``backup-s3`` extra (boto3) is not
  installed; the flat-file client can't reach S3 without it.

Manual triggers are wired the same way as the other jobs and surface
through ``/api/system/jobs/options_history_keep_current/run``.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas_market_calendars as mcal
from sqlalchemy.orm import Session

from core.config import Settings, get_settings
from core.logging import get_logger
from core.time import market_today
from ingestion.options_history import backfill_history_flatfile
from scheduler.context import job_run

log = get_logger(__name__)

JOB_NAME = "options_history_keep_current"


def run_options_history_keep_current(
    session: Session,
    *,
    settings: Settings | None = None,
    market_calendar: str | None = "NYSE",
    as_of: date | None = None,
) -> None:
    """Backfill yesterday's option chain rows. Idempotent — re-runs upsert."""
    cfg = settings or get_settings()
    today = as_of or market_today()
    target_day = today - timedelta(days=1)

    with job_run(session, JOB_NAME) as ctx:
        if market_calendar and not _is_trading_day(market_calendar, target_day):
            log.info(
                "options_history_keep_current.holiday_skip",
                target=target_day.isoformat(),
                calendar=market_calendar,
            )
            ctx.set_result(skipped="holiday", date=target_day.isoformat())
            return

        if not (
            cfg.polygon_flatfiles_access_key_id and cfg.polygon_flatfiles_secret_access_key
        ):
            log.warning("options_history_keep_current.no_credentials")
            ctx.set_result(skipped="no_credentials", date=target_day.isoformat())
            return

        try:
            from ingestion.polygon_flatfiles import PolygonFlatFileClient
        except ImportError as exc:
            log.warning("options_history_keep_current.boto3_missing", error=str(exc))
            ctx.set_result(skipped="boto3_missing", date=target_day.isoformat())
            return

        client = PolygonFlatFileClient()
        summary = backfill_history_flatfile(
            session,
            client,
            symbols=None,
            start=target_day,
            end=target_day,
        )

        if summary.symbols_requested == 0:
            log.warning("options_history_keep_current.no_active_tickers")
            ctx.set_result(
                skipped="no_active_tickers",
                date=target_day.isoformat(),
            )
            return

        ctx.set_result(
            date=target_day.isoformat(),
            symbols=summary.symbols_requested,
            symbols_with_data=summary.symbols_with_data,
            contracts=summary.contracts_fetched,
            rows=summary.rows_written,
        )


def _is_trading_day(calendar_name: str, day: date) -> bool:
    calendar = mcal.get_calendar(calendar_name)
    schedule = calendar.schedule(start_date=day, end_date=day)
    return bool(not schedule.empty)
