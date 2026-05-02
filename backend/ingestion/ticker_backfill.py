"""One-shot backfill for a single ticker, wrapped in ``job_run`` accounting.

Used by ``POST /api/tickers`` to bring a freshly-added symbol up to date —
runs the same steps as the daily evening pipeline but pinned to one
symbol and skipping macro (which is global, not per-symbol).

Steps: full bars history (~5 years) → indicators → options chain + IV →
earnings (next 90 days) → sector/market_cap profile from Finnhub.
"""

from __future__ import annotations

from core.logging import get_logger
from db import get_session
from ingestion.bars import DEFAULT_FULL_YEARS
from ingestion.pipeline import _build_earnings_client, run_full
from ingestion.ticker_metadata import _build_client as _build_metadata_client
from ingestion.ticker_metadata import fetch_metadata
from scheduler.app import build_alpaca_client, build_options_client
from scheduler.context import job_run

log = get_logger(__name__)

JOB_NAME = "ticker_backfill"


def run_ticker_backfill(symbol: str) -> None:
    """Backfill one symbol end-to-end, recording the run in ``job_runs``."""
    sym = symbol.upper()
    with get_session() as session, job_run(session, JOB_NAME) as ctx:
        ctx.set_result(symbol=sym)

        alpaca = build_alpaca_client()
        if alpaca is None:
            log.warning("ticker_backfill.no_alpaca_creds_skipped", symbol=sym)
            ctx.set_result(skipped="no_alpaca_creds")
            return

        options = build_options_client()
        earnings = _build_earnings_client()

        summary = run_full(
            session,
            alpaca,
            [sym],
            years=DEFAULT_FULL_YEARS,
            options_client=options,
            skip_options=options is None,
            earnings_client=earnings,
            skip_earnings=earnings is None,
            skip_macro=True,
        )

        metadata = _build_metadata_client()
        metadata_updated = 0
        if metadata is not None:
            md_summary = fetch_metadata(session, metadata, symbols=[sym])
            metadata_updated = md_summary.rows_updated

        ctx.set_result(
            bars=summary.fetch.bars_written,
            indicators=summary.indicators_written,
            iv_rows=summary.iv.iv_rows_written,
            options_contracts=(
                summary.options.contracts_written if summary.options is not None else 0
            ),
            earnings_rows=(summary.earnings.rows_written if summary.earnings is not None else 0),
            metadata_updated=metadata_updated,
        )
