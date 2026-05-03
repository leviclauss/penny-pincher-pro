"""Earnings calendar fetcher.

Pulls the next ``EARNINGS_LOOKAHEAD_DAYS`` of earnings dates from Finnhub
for each active ticker and upserts them into the ``earnings`` table keyed
by ``(symbol, earnings_date)``. Re-running on the same window is
idempotent and refreshes ``time_of_day`` if Finnhub revised it.

Strategy (see docs/ops/api-rate-limits.md):

- **Default: per-symbol query, throttled by ``FinnhubClient._RateLimiter``.**
  At the free tier's 55 cpm cap, ~110 symbols take ~2 minutes nightly —
  acceptable, and avoids the bulk endpoint's correctness pitfalls (next
  point). The rate limiter is what actually solved the original
  rate-limit problem; bulk was only a runtime optimization on top.
- Optional bulk-first (``finnhub_earnings_use_bulk=True``): one bulk
  ``calendar/earnings`` call for the window, filtered to the active set
  in Python, with per-symbol fallback for symbols *entirely absent* from
  bulk. Faster (1 req + N fallbacks) but **not safe to default**: bulk
  silently drops some reports — observed with MSTR's near-term earnings
  missing from bulk while the per-symbol query returned it correctly.
  Worse, the fallback only catches *missing* symbols; if bulk returns a
  *later* date for a symbol whose earlier report it dropped, we'd persist
  the wrong "next earnings" date. Only enable after validating bulk vs
  per-symbol for your universe.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.config import get_settings
from core.logging import get_logger
from core.time import utcnow
from db.models.market import Earnings, Ticker
from ingestion.finnhub_client import EarningsRecord

log = get_logger(__name__)

DEFAULT_LOOKAHEAD_DAYS = 90


class EarningsSource(Protocol):
    """Anything that returns ``EarningsRecord``s for a date window."""

    def get_earnings_calendar(
        self,
        *,
        from_date: date,
        to_date: date,
        symbol: str | None = ...,
    ) -> list[EarningsRecord]: ...


@dataclass
class EarningsFetchSummary:
    symbols_in_window: int
    rows_written: int
    window_from: date
    window_to: date
    bulk_used: bool = False
    fallback_calls: int = 0


def fetch_earnings(
    session: Session,
    client: EarningsSource,
    symbols: list[str] | None = None,
    *,
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
    as_of: date | None = None,
    use_bulk: bool | None = None,
) -> EarningsFetchSummary:
    """Fetch + persist earnings for active tickers over the next ``lookahead_days``."""
    target = _resolve_symbols(session, symbols)
    today = as_of or _today_utc()
    to_date = today + timedelta(days=lookahead_days)

    bulk_enabled = use_bulk if use_bulk is not None else get_settings().finnhub_earnings_use_bulk

    bulk_used = False
    fallback_calls = 0
    if bulk_enabled:
        collected, fallback_calls = _fetch_bulk_then_fallback(client, target, today, to_date)
        bulk_used = True
    else:
        collected = _fetch_per_symbol(client, target, today, to_date)

    fetched_at = utcnow()
    written = _upsert_earnings(session, collected, fetched_at) if collected else 0
    if written:
        session.commit()

    in_window = len({r.symbol for r in collected})
    log.info(
        "earnings.fetch.done",
        symbols=len(target),
        in_window=in_window,
        rows=written,
        bulk_used=bulk_used,
        fallback_calls=fallback_calls,
    )
    return EarningsFetchSummary(
        symbols_in_window=in_window,
        rows_written=written,
        window_from=today,
        window_to=to_date,
        bulk_used=bulk_used,
        fallback_calls=fallback_calls,
    )


def _fetch_bulk_then_fallback(
    client: EarningsSource,
    target: list[str],
    from_date: date,
    to_date: date,
) -> tuple[list[EarningsRecord], int]:
    """One bulk call, then per-symbol top-ups for active symbols missing from it."""
    target_set = set(target)
    bulk_records = client.get_earnings_calendar(from_date=from_date, to_date=to_date)
    collected = [r for r in bulk_records if r.symbol in target_set]

    seen = {r.symbol for r in collected}
    missing = sorted(target_set - seen)
    fallback_calls = 0
    for sym in missing:
        extra = client.get_earnings_calendar(from_date=from_date, to_date=to_date, symbol=sym)
        fallback_calls += 1
        collected.extend(r for r in extra if r.symbol == sym)
    if missing:
        log.info(
            "earnings.bulk_fallback",
            missing_count=len(missing),
            sample=missing[:5],
        )
    return collected, fallback_calls


def _fetch_per_symbol(
    client: EarningsSource,
    target: list[str],
    from_date: date,
    to_date: date,
) -> list[EarningsRecord]:
    collected: list[EarningsRecord] = []
    for sym in target:
        records = client.get_earnings_calendar(from_date=from_date, to_date=to_date, symbol=sym)
        collected.extend(r for r in records if r.symbol == sym)
    return collected


def _upsert_earnings(
    session: Session, records: Iterable[EarningsRecord], fetched_at: datetime
) -> int:
    rows = [
        {
            "symbol": r.symbol,
            "earnings_date": r.earnings_date,
            "time_of_day": r.time_of_day,
            "fetched_at": fetched_at,
        }
        for r in records
    ]
    if not rows:
        return 0
    stmt = sqlite_insert(Earnings).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Earnings.symbol, Earnings.earnings_date],
        set_={
            "time_of_day": stmt.excluded.time_of_day,
            "fetched_at": stmt.excluded.fetched_at,
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


def _today_utc() -> date:
    return utcnow().date()
