"""Screener job — runs after the evening ingestion pipeline.

Loads every active config and evaluates it against the active watchlist for
the current market date. Wraps in ``job_run`` so successes and failures land
in ``job_runs`` like every other scheduled job.

Scheduled 30 min after ``evening_pipeline`` so bars/indicators/options are
in place before filters run. Holiday handling matches the evening job: when
``market_calendar`` resolves to an NYSE-closed day, the job records a
``skipped="holiday"`` row and returns without touching the screener.
"""

from __future__ import annotations

from datetime import date

import pandas_market_calendars as mcal
from sqlalchemy.orm import Session

from core.logging import get_logger
from core.time import market_today
from scheduler.context import job_run
from screener.pipeline import run_screener

log = get_logger(__name__)

JOB_NAME = "screener_pipeline"


def run_screener_job(
    session: Session,
    *,
    market_calendar: str | None = None,
    as_of: date | None = None,
) -> None:
    """Run the screener once for ``as_of`` (default: today in market TZ)."""
    today = as_of or market_today()

    if market_calendar and not _is_trading_day(market_calendar, today):
        log.info("screener_pipeline.holiday_skip", date=str(today), calendar=market_calendar)
        with job_run(session, JOB_NAME) as ctx:
            ctx.set_result(skipped="holiday", date=today.isoformat())
        return

    with job_run(session, JOB_NAME) as ctx:
        summary = run_screener(session, as_of=today)
        ctx.set_result(
            as_of=today.isoformat(),
            configs_run=summary.configs_run,
            rows_written=summary.rows_written,
            per_config=[
                {
                    "config_id": c.config_id,
                    "config_name": c.config_name,
                    "evaluated": c.symbols_evaluated,
                    "passed": c.symbols_passed,
                    "dropped_by_sector": c.symbols_dropped_by_sector,
                }
                for c in summary.per_config
            ],
        )


def _is_trading_day(calendar_name: str, day: date) -> bool:
    calendar = mcal.get_calendar(calendar_name)
    schedule = calendar.schedule(start_date=day, end_date=day)
    return bool(not schedule.empty)
