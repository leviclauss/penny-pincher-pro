"""Ingestion orchestration + CLI.

The pipeline is the public entry point used by the scheduler (later) and the
``python -m ingestion.pipeline`` CLI today. Steps:

    1. Fetch daily bars (full backfill or incremental delta).
    2. For each affected symbol, load full bar history, compute indicators
       over the entire series, and upsert the affected date range into
       ``indicators_daily``.

For ``--full``: every symbol gets every indicator row written. For
``--incremental``: only dates newly added by step 1 are written, but the
indicator computation still uses the full history (EMAs need warm-up).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import click
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import get_settings
from core.logging import configure_logging, get_logger
from db import get_session
from db.models.market import BarDaily, IndicatorDaily
from ingestion.alpaca_client import AlpacaClient
from ingestion.bars import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_FULL_YEARS,
    FetchSummary,
    fetch_full,
    fetch_incremental,
)
from ingestion.indicators import compute_indicators
from ingestion.persistence import load_bars, upsert_indicators

log = get_logger(__name__)


@dataclass
class PipelineSummary:
    mode: str
    fetch: FetchSummary
    indicators_written: int
    symbols_processed: int


def run_full(
    session: Session,
    client: AlpacaClient,
    symbols: list[str] | None = None,
    *,
    years: int = DEFAULT_FULL_YEARS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    end: date | None = None,
) -> PipelineSummary:
    fetch_summary = fetch_full(
        session, client, symbols, years=years, batch_size=batch_size, end=end
    )
    affected = symbols or _affected_after_fetch(session, fetch_summary)
    written, processed = _refresh_indicators(session, affected, only_new=False)
    log.info("pipeline.full.done", indicators=written, symbols=processed)
    return PipelineSummary(
        mode="full", fetch=fetch_summary, indicators_written=written, symbols_processed=processed
    )


def run_incremental(
    session: Session,
    client: AlpacaClient,
    symbols: list[str] | None = None,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    end: date | None = None,
) -> PipelineSummary:
    fetch_summary = fetch_incremental(session, client, symbols, batch_size=batch_size, end=end)
    affected = symbols or _affected_after_fetch(session, fetch_summary)
    written, processed = _refresh_indicators(session, affected, only_new=True)
    log.info("pipeline.incremental.done", indicators=written, symbols=processed)
    return PipelineSummary(
        mode="incremental",
        fetch=fetch_summary,
        indicators_written=written,
        symbols_processed=processed,
    )


def _affected_after_fetch(session: Session, summary: FetchSummary) -> list[str]:
    """When the caller didn't pin symbols, recompute indicators for any symbol
    that has bars stored — keeps the indicator table consistent with bars."""
    _ = summary
    rows = session.execute(select(BarDaily.symbol).distinct()).all()
    return sorted(r[0] for r in rows)


def _refresh_indicators(
    session: Session, symbols: Iterable[str], *, only_new: bool
) -> tuple[int, int]:
    written = 0
    processed = 0
    for symbol in symbols:
        bars = load_bars(session, symbol)
        if bars.empty:
            continue
        indicators = compute_indicators(bars)

        only_dates: list[date] | None = None
        if only_new:
            only_dates = _new_indicator_dates(session, symbol, indicators.index)

        written += upsert_indicators(session, symbol, indicators, only_dates=only_dates)
        processed += 1
        session.commit()
    return written, processed


def _new_indicator_dates(session: Session, symbol: str, candidate_index: pd.Index) -> list[date]:
    existing = set(
        session.execute(select(IndicatorDaily.date).where(IndicatorDaily.symbol == symbol))
        .scalars()
        .all()
    )
    out: list[date] = []
    for idx in candidate_index:
        d = idx.date() if isinstance(idx, pd.Timestamp) else idx
        if isinstance(d, date) and d not in existing:
            out.append(d)
    return out


@click.command(context_settings={"show_default": True})
@click.option(
    "--full/--incremental",
    "full",
    default=None,
    required=True,
    help="Run full backfill or incremental update.",
)
@click.option(
    "--symbols",
    "symbols",
    default=None,
    help="Comma-separated symbols; default = every active ticker.",
)
@click.option(
    "--years",
    type=int,
    default=DEFAULT_FULL_YEARS,
    help="Years of history to backfill (full mode only).",
)
@click.option(
    "--batch-size",
    type=int,
    default=DEFAULT_BATCH_SIZE,
    help="Symbols per Alpaca request.",
)
def cli(full: bool, symbols: str | None, years: int, batch_size: int) -> None:
    """Run the ingestion pipeline end-to-end."""
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)

    symbol_list = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    client = AlpacaClient()

    with get_session() as session:
        if full:
            summary = run_full(session, client, symbol_list, years=years, batch_size=batch_size)
        else:
            summary = run_incremental(session, client, symbol_list, batch_size=batch_size)

    click.echo(
        f"mode={summary.mode} "
        f"bars_written={summary.fetch.bars_written} "
        f"indicators_written={summary.indicators_written} "
        f"symbols={summary.symbols_processed}"
    )


if __name__ == "__main__":
    cli()
