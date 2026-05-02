"""Ingestion orchestration + CLI.

The pipeline is the public entry point used by the scheduler (later) and the
``python -m ingestion.pipeline`` CLI today. Steps:

    1. Fetch daily bars (full backfill or incremental delta).
    2. For each affected symbol, load full bar history, compute indicators
       over the entire series, and upsert the affected date range into
       ``indicators_daily``.
    3. (Unless ``--skip-options``) Fetch current option chains, compute
       ATM IV / IV rank / IV percentile, upsert the IV columns into
       ``indicators_daily`` for ``as_of``.
    4. (Unless ``--skip-earnings``) Pull the next ~90 days of earnings
       from Finnhub, filter to active tickers, upsert into ``earnings``.
       Silently skipped when no FINNHUB_API_KEY is configured.
    5. (Unless ``--skip-macro``) Pull VIX/VIX9D from Yahoo, compose with
       SPY close + 200 EMA from local data, upsert ``macro_daily``.

Step 5 must run after step 2 because it reads SPY's EMA from
``indicators_daily``.

For ``--full``: every symbol gets every indicator row written. For
``--incremental``: only dates newly added by step 1 are written, but the
indicator computation still uses the full history (EMAs need warm-up).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

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
from ingestion.earnings import EarningsFetchSummary, EarningsSource, fetch_earnings
from ingestion.finnhub_client import FinnhubClient, FinnhubError
from ingestion.indicators import compute_indicators
from ingestion.iv import compute_atm_iv, compute_iv_percentile, compute_iv_rank
from ingestion.macro import IndexHistorySource, MacroFetchSummary, fetch_macro
from ingestion.options import ChainSource, OptionsFetchSummary, fetch_chains
from ingestion.options_client import AlpacaOptionsClient
from ingestion.persistence import (
    load_bars,
    load_iv_history,
    load_options_chain,
    upsert_indicators,
    upsert_iv_indicators,
)
from ingestion.yahoo_client import YahooClient

log = get_logger(__name__)


@dataclass
class IVSummary:
    symbols_processed: int = 0
    iv_rows_written: int = 0


@dataclass
class PipelineSummary:
    mode: str
    fetch: FetchSummary
    indicators_written: int
    symbols_processed: int
    options: OptionsFetchSummary | None = None
    iv: IVSummary = field(default_factory=IVSummary)
    earnings: EarningsFetchSummary | None = None
    macro: MacroFetchSummary | None = None


def run_full(
    session: Session,
    client: AlpacaClient,
    symbols: list[str] | None = None,
    *,
    years: int = DEFAULT_FULL_YEARS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    end: date | None = None,
    options_client: ChainSource | None = None,
    skip_options: bool = False,
    earnings_client: EarningsSource | None = None,
    skip_earnings: bool = False,
    macro_client: IndexHistorySource | None = None,
    skip_macro: bool = False,
) -> PipelineSummary:
    fetch_summary = fetch_full(
        session, client, symbols, years=years, batch_size=batch_size, end=end
    )
    affected = symbols or _affected_after_fetch(session, fetch_summary)
    written, processed = _refresh_indicators(session, affected, only_new=False)
    log.info("pipeline.full.indicators_done", indicators=written, symbols=processed)

    options_summary, iv_summary = _maybe_run_options_and_iv(
        session, options_client, affected, skip_options=skip_options, as_of=end
    )
    earnings_summary = _maybe_run_earnings(
        session, earnings_client, symbols, skip_earnings=skip_earnings, as_of=end
    )
    macro_summary = _maybe_run_macro(session, macro_client, skip_macro=skip_macro, as_of=end)

    log.info("pipeline.full.done")
    return PipelineSummary(
        mode="full",
        fetch=fetch_summary,
        indicators_written=written,
        symbols_processed=processed,
        options=options_summary,
        iv=iv_summary,
        earnings=earnings_summary,
        macro=macro_summary,
    )


def run_incremental(
    session: Session,
    client: AlpacaClient,
    symbols: list[str] | None = None,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    end: date | None = None,
    options_client: ChainSource | None = None,
    skip_options: bool = False,
    earnings_client: EarningsSource | None = None,
    skip_earnings: bool = False,
    macro_client: IndexHistorySource | None = None,
    skip_macro: bool = False,
) -> PipelineSummary:
    fetch_summary = fetch_incremental(session, client, symbols, batch_size=batch_size, end=end)
    affected = symbols or _affected_after_fetch(session, fetch_summary)
    written, processed = _refresh_indicators(session, affected, only_new=True)
    log.info("pipeline.incremental.indicators_done", indicators=written, symbols=processed)

    options_summary, iv_summary = _maybe_run_options_and_iv(
        session, options_client, affected, skip_options=skip_options, as_of=end
    )
    earnings_summary = _maybe_run_earnings(
        session, earnings_client, symbols, skip_earnings=skip_earnings, as_of=end
    )
    macro_summary = _maybe_run_macro(session, macro_client, skip_macro=skip_macro, as_of=end)

    log.info("pipeline.incremental.done")
    return PipelineSummary(
        mode="incremental",
        fetch=fetch_summary,
        indicators_written=written,
        symbols_processed=processed,
        options=options_summary,
        iv=iv_summary,
        earnings=earnings_summary,
        macro=macro_summary,
    )


def _maybe_run_options_and_iv(
    session: Session,
    options_client: ChainSource | None,
    symbols: list[str],
    *,
    skip_options: bool,
    as_of: date | None,
) -> tuple[OptionsFetchSummary | None, IVSummary]:
    if skip_options or options_client is None:
        if not skip_options:
            log.info("pipeline.options_skipped", reason="no_client")
        return None, IVSummary()

    options_summary = fetch_chains(session, options_client, symbols, as_of=as_of)
    iv_summary = _refresh_iv(session, symbols, as_of=as_of or _today_utc())
    return options_summary, iv_summary


def _maybe_run_earnings(
    session: Session,
    earnings_client: EarningsSource | None,
    symbols: list[str] | None,
    *,
    skip_earnings: bool,
    as_of: date | None,
) -> EarningsFetchSummary | None:
    if skip_earnings or earnings_client is None:
        if not skip_earnings:
            log.info("pipeline.earnings_skipped", reason="no_client")
        return None
    settings = get_settings()
    return fetch_earnings(
        session,
        earnings_client,
        symbols,
        lookahead_days=settings.earnings_lookahead_days,
        as_of=as_of,
    )


def _maybe_run_macro(
    session: Session,
    macro_client: IndexHistorySource | None,
    *,
    skip_macro: bool,
    as_of: date | None,
) -> MacroFetchSummary | None:
    if skip_macro or macro_client is None:
        if not skip_macro:
            log.info("pipeline.macro_skipped", reason="no_client")
        return None
    settings = get_settings()
    return fetch_macro(
        session,
        macro_client,
        lookback_days=settings.macro_lookback_days,
        as_of=as_of,
        spy_symbol=settings.spy_symbol,
    )


def _refresh_iv(session: Session, symbols: Iterable[str], *, as_of: date) -> IVSummary:
    settings = get_settings()
    summary = IVSummary()
    for symbol in symbols:
        chain = load_options_chain(session, symbol)
        if not chain:
            continue
        spot = _latest_close(session, symbol)
        if spot is None:
            continue

        atm = compute_atm_iv(chain, spot=spot, as_of=as_of, risk_free_rate=settings.risk_free_rate)
        if atm is None:
            log.info("iv.no_atm_skipped", symbol=symbol)
            continue

        history = load_iv_history(session, symbol, before=as_of)
        rank = compute_iv_rank(history, atm)
        pct = compute_iv_percentile(history, atm)

        upsert_iv_indicators(session, symbol, as_of, iv_atm=atm, iv_rank=rank, iv_percentile=pct)
        summary.symbols_processed += 1
        summary.iv_rows_written += 1
        session.commit()

    log.info("pipeline.iv_done", symbols=summary.symbols_processed)
    return summary


def _latest_close(session: Session, symbol: str) -> float | None:
    row = session.execute(
        select(BarDaily.close)
        .where(BarDaily.symbol == symbol)
        .order_by(BarDaily.date.desc())
        .limit(1)
    ).first()
    return float(row[0]) if row else None


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


def _today_utc() -> date:
    return datetime.now(UTC).date()


def _build_earnings_client() -> EarningsSource | None:
    """Construct a Finnhub client if a key is configured; otherwise None."""
    try:
        return FinnhubClient()
    except FinnhubError:
        log.info("pipeline.finnhub_unavailable", reason="missing_api_key")
        return None


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
@click.option(
    "--skip-options",
    is_flag=True,
    default=False,
    help="Skip options chain fetch + IV computation (fast iteration on bars).",
)
@click.option(
    "--skip-earnings",
    is_flag=True,
    default=False,
    help="Skip earnings calendar fetch.",
)
@click.option(
    "--skip-macro",
    is_flag=True,
    default=False,
    help="Skip VIX/SPY macro fetch.",
)
def cli(
    full: bool,
    symbols: str | None,
    years: int,
    batch_size: int,
    skip_options: bool,
    skip_earnings: bool,
    skip_macro: bool,
) -> None:
    """Run the ingestion pipeline end-to-end."""
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)

    symbol_list = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    client = AlpacaClient()
    options_client: ChainSource | None = None if skip_options else AlpacaOptionsClient()
    earnings_client: EarningsSource | None = None if skip_earnings else _build_earnings_client()
    macro_client: IndexHistorySource | None = None if skip_macro else YahooClient()

    with get_session() as session:
        if full:
            summary = run_full(
                session,
                client,
                symbol_list,
                years=years,
                batch_size=batch_size,
                options_client=options_client,
                skip_options=skip_options,
                earnings_client=earnings_client,
                skip_earnings=skip_earnings,
                macro_client=macro_client,
                skip_macro=skip_macro,
            )
        else:
            summary = run_incremental(
                session,
                client,
                symbol_list,
                batch_size=batch_size,
                options_client=options_client,
                skip_options=skip_options,
                earnings_client=earnings_client,
                skip_earnings=skip_earnings,
                macro_client=macro_client,
                skip_macro=skip_macro,
            )

    options_msg = (
        f"options_contracts={summary.options.contracts_written}"
        if summary.options
        else "options=skipped"
    )
    earnings_msg = (
        f"earnings_rows={summary.earnings.rows_written}" if summary.earnings else "earnings=skipped"
    )
    macro_msg = f"macro_rows={summary.macro.rows_written}" if summary.macro else "macro=skipped"
    click.echo(
        f"mode={summary.mode} "
        f"bars_written={summary.fetch.bars_written} "
        f"indicators_written={summary.indicators_written} "
        f"symbols={summary.symbols_processed} "
        f"{options_msg} "
        f"iv_rows={summary.iv.iv_rows_written} "
        f"{earnings_msg} "
        f"{macro_msg}"
    )


if __name__ == "__main__":
    cli()
