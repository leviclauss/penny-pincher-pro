"""Universe scan job — syncs S&P 100 tickers and runs the screener against them.

Runs after ``screener_pipeline`` so the active configs are in place.  Because
universe tickers have ``is_active=True``, the evening pipeline has already
ingested their bars/indicators/options before this job fires.

The job always passes the universe symbol list explicitly to ``run_screener``
so hidden universe tickers (``is_hidden=True``) are included even though the
default watchlist screener excludes them.
"""

from __future__ import annotations

from datetime import date

import pandas_market_calendars as mcal
from sqlalchemy.orm import Session

from core.logging import get_logger
from core.time import market_today
from ingestion.universe import get_universe_symbols, sync_universe_tickers
from scheduler.context import job_run
from screener.pipeline import run_screener

log = get_logger(__name__)

JOB_NAME = "universe_scan"


def run_universe_scan_job(
    session: Session,
    *,
    market_calendar: str | None = None,
    as_of: date | None = None,
) -> None:
    """Sync universe tickers, then run the screener against all of them."""
    today = as_of or market_today()

    if market_calendar and not _is_trading_day(market_calendar, today):
        log.info("universe_scan.holiday_skip", date=str(today), calendar=market_calendar)
        with job_run(session, JOB_NAME) as ctx:
            ctx.set_result(skipped="holiday", date=today.isoformat())
        return

    with job_run(session, JOB_NAME) as ctx:
        sync_summary = sync_universe_tickers(session)
        symbols = get_universe_symbols(session)

        if not symbols:
            log.warning("universe_scan.no_symbols")
            ctx.set_result(as_of=today.isoformat(), skipped="no_universe_symbols")
            return

        screener_summary = run_screener(session, as_of=today, symbols=symbols)
        ctx.set_result(
            as_of=today.isoformat(),
            universe_size=len(symbols),
            universe_inserted=sync_summary.inserted,
            configs_run=screener_summary.configs_run,
            rows_written=screener_summary.rows_written,
            per_config=[
                {
                    "config_id": c.config_id,
                    "config_name": c.config_name,
                    "evaluated": c.symbols_evaluated,
                    "passed": c.symbols_passed,
                    "dropped_by_sector": c.symbols_dropped_by_sector,
                }
                for c in screener_summary.per_config
            ],
        )


def _is_trading_day(calendar_name: str, day: date) -> bool:
    calendar = mcal.get_calendar(calendar_name)
    schedule = calendar.schedule(start_date=day, end_date=day)
    return bool(not schedule.empty)
