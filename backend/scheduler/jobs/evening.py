"""Evening pipeline job — runs after the cash close to refresh all data.

Single ordered job (per doc 07's "evening pipeline" pattern) so that if any
step fails, downstream steps don't silently process stale inputs. Wraps
``ingestion.pipeline.run_incremental`` in ``job_run`` so every execution
lands in ``job_runs`` with metrics or an error.

Holiday handling: when ``market_calendar`` resolves to an NYSE-closed day,
the job records a ``skipped="holiday"`` row and returns without hitting
external APIs.
"""

from __future__ import annotations

from datetime import date

import pandas_market_calendars as mcal
from sqlalchemy.orm import Session

from core.logging import get_logger
from core.time import utcnow
from ingestion.alpaca_client import AlpacaClient
from ingestion.earnings import EarningsSource
from ingestion.macro import IndexHistorySource
from ingestion.options import ChainSource
from ingestion.pipeline import run_incremental
from scheduler.context import job_run

log = get_logger(__name__)

JOB_NAME = "evening_pipeline"


def run_evening_pipeline(
    session: Session,
    *,
    alpaca_client: AlpacaClient,
    options_client: ChainSource | None = None,
    earnings_client: EarningsSource | None = None,
    macro_client: IndexHistorySource | None = None,
    market_calendar: str | None = None,
    as_of: date | None = None,
) -> None:
    """Run the post-close pipeline once.

    Idempotent — re-running on the same calendar day re-triggers ingestion
    (bars short-circuit when up to date; options/IV refresh by design).
    """
    today = as_of or utcnow().date()

    if market_calendar and not _is_trading_day(market_calendar, today):
        log.info("evening_pipeline.holiday_skip", date=str(today), calendar=market_calendar)
        with job_run(session, JOB_NAME) as ctx:
            ctx.set_result(skipped="holiday", date=today.isoformat())
        return

    with job_run(session, JOB_NAME) as ctx:
        summary = run_incremental(
            session,
            alpaca_client,
            options_client=options_client,
            skip_options=options_client is None,
            earnings_client=earnings_client,
            skip_earnings=earnings_client is None,
            macro_client=macro_client,
            skip_macro=macro_client is None,
        )
        ctx.set_result(
            mode=summary.mode,
            bars=summary.fetch.bars_written,
            indicators=summary.indicators_written,
            symbols=summary.symbols_processed,
            options_contracts=(
                summary.options.contracts_written if summary.options is not None else 0
            ),
            iv_rows=summary.iv.iv_rows_written,
            earnings_rows=(summary.earnings.rows_written if summary.earnings is not None else 0),
            macro_rows=(summary.macro.rows_written if summary.macro is not None else 0),
            as_of=today.isoformat(),
        )


def _is_trading_day(calendar_name: str, day: date) -> bool:
    calendar = mcal.get_calendar(calendar_name)
    schedule = calendar.schedule(start_date=day, end_date=day)
    return bool(not schedule.empty)
