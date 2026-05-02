"""Daily bars fetcher: full backfill and incremental update.

Reads through the Alpaca client wrapper, persists to ``bars_daily`` via an
idempotent upsert. Symbol batching is handled here (Alpaca free tier permits
multi-symbol requests; we cap chunk size to stay well under rate limits).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pandas_market_calendars as mcal
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.logging import get_logger
from db.models.market import BarDaily, Ticker
from ingestion.alpaca_client import AlpacaClient, BarRecord

log = get_logger(__name__)

DEFAULT_BATCH_SIZE = 50
DEFAULT_FULL_YEARS = 5
TRADING_DAYS_PER_YEAR = 252


@dataclass
class FetchSummary:
    symbols_requested: int
    symbols_with_data: int
    bars_written: int
    earliest: date | None
    latest: date | None


def fetch_full(
    session: Session,
    client: AlpacaClient,
    symbols: list[str] | None = None,
    *,
    years: int = DEFAULT_FULL_YEARS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    end: date | None = None,
) -> FetchSummary:
    """Backfill ``years`` of daily bars for ``symbols`` (or every active ticker)."""
    target = _resolve_symbols(session, symbols)
    end_date = end or _latest_available_session()
    start_date = end_date - timedelta(days=int(years * 365.25) + 7)
    log.info("bars.fetch_full.start", symbols=len(target), start=str(start_date), end=str(end_date))
    return _fetch_and_upsert(session, client, target, start_date, end_date, batch_size)


def fetch_incremental(
    session: Session,
    client: AlpacaClient,
    symbols: list[str] | None = None,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    end: date | None = None,
    fallback_days: int = 30,
) -> FetchSummary:
    """Fetch only bars newer than what we already have.

    Symbols are grouped by their last-stored date so each batch issues a single
    request from ``last_date + 1`` to ``end``. Symbols with no prior data fall
    back to the last ``fallback_days`` days (use ``fetch_full`` for true cold
    starts; this is meant for the daily incremental run).
    """
    target = _resolve_symbols(session, symbols)
    end_date = end or _latest_available_session()
    last_dates = _last_stored_dates(session, target)

    summaries: list[FetchSummary] = []
    for start_date, group in _group_by_start(target, last_dates, end_date, fallback_days):
        if start_date > end_date:
            log.info("bars.fetch_incremental.up_to_date", symbols=len(group), start=str(start_date))
            continue
        summaries.append(
            _fetch_and_upsert(session, client, group, start_date, end_date, batch_size)
        )

    return _merge(summaries, len(target))


def _fetch_and_upsert(
    session: Session,
    client: AlpacaClient,
    symbols: list[str],
    start: date,
    end: date,
    batch_size: int,
) -> FetchSummary:
    bars_written = 0
    earliest: date | None = None
    latest: date | None = None
    symbols_with_data = 0

    for chunk in _chunk(symbols, batch_size):
        bars_by_symbol = client.get_daily_bars(list(chunk), start=start, end=end)
        for _symbol, bars in bars_by_symbol.items():
            if not bars:
                continue
            symbols_with_data += 1
            bars_written += _upsert_bars(session, bars)
            chunk_min = bars[0].date
            chunk_max = bars[-1].date
            earliest = chunk_min if earliest is None or chunk_min < earliest else earliest
            latest = chunk_max if latest is None or chunk_max > latest else latest
        session.commit()

    log.info(
        "bars.fetch.done",
        symbols=len(symbols),
        with_data=symbols_with_data,
        bars=bars_written,
        earliest=str(earliest) if earliest else None,
        latest=str(latest) if latest else None,
    )
    return FetchSummary(
        symbols_requested=len(symbols),
        symbols_with_data=symbols_with_data,
        bars_written=bars_written,
        earliest=earliest,
        latest=latest,
    )


def _upsert_bars(session: Session, bars: list[BarRecord]) -> int:
    """SQLite ON CONFLICT upsert keyed by (symbol, date)."""
    rows = [
        {
            "symbol": b.symbol,
            "date": b.date,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
        }
        for b in bars
    ]
    if not rows:
        return 0
    stmt = sqlite_insert(BarDaily).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[BarDaily.symbol, BarDaily.date],
        set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "volume": stmt.excluded.volume,
        },
    )
    session.execute(stmt)
    return len(rows)


def _resolve_symbols(session: Session, symbols: list[str] | None) -> list[str]:
    if symbols:
        return symbols
    rows = session.execute(
        select(Ticker.symbol).where(Ticker.is_active.is_(True)).order_by(Ticker.symbol)
    ).all()
    return [r[0] for r in rows]


def _last_stored_dates(session: Session, symbols: list[str]) -> dict[str, date]:
    if not symbols:
        return {}
    rows = session.execute(
        select(BarDaily.symbol, func.max(BarDaily.date))
        .where(BarDaily.symbol.in_(symbols))
        .group_by(BarDaily.symbol)
    ).all()
    return {sym: dt for sym, dt in rows if dt is not None}


def _group_by_start(
    symbols: list[str],
    last_dates: dict[str, date],
    end_date: date,
    fallback_days: int,
) -> Iterator[tuple[date, list[str]]]:
    fallback_start = end_date - timedelta(days=fallback_days)
    grouped: dict[date, list[str]] = {}
    for symbol in symbols:
        last = last_dates.get(symbol)
        start = (last + timedelta(days=1)) if last else fallback_start
        grouped.setdefault(start, []).append(symbol)
    for start, group in sorted(grouped.items()):
        yield start, sorted(group)


def _chunk(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _merge(summaries: Iterable[FetchSummary], symbols_requested: int) -> FetchSummary:
    bars = 0
    with_data = 0
    earliest: date | None = None
    latest: date | None = None
    for s in summaries:
        bars += s.bars_written
        with_data += s.symbols_with_data
        if s.earliest and (earliest is None or s.earliest < earliest):
            earliest = s.earliest
        if s.latest and (latest is None or s.latest > latest):
            latest = s.latest
    return FetchSummary(
        symbols_requested=symbols_requested,
        symbols_with_data=with_data,
        bars_written=bars,
        earliest=earliest,
        latest=latest,
    )


def _latest_available_session(calendar_name: str = "XNYS") -> date:
    """Most recent NYSE session whose close has already elapsed.

    Returns today's date if the regular session has closed; otherwise the prior
    trading day (skipping weekends and exchange holidays).
    """
    calendar = mcal.get_calendar(calendar_name)
    now = datetime.now(UTC)
    schedule = calendar.schedule(
        start_date=(now - timedelta(days=10)).date(),
        end_date=now.date(),
    )
    closed = schedule[schedule["market_close"] <= now]
    if closed.empty:
        return (now - timedelta(days=1)).date()
    return closed.index[-1].date()  # type: ignore[no-any-return]
