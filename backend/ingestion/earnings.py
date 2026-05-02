"""Earnings calendar fetcher.

Pulls the next ``EARNINGS_LOOKAHEAD_DAYS`` of earnings dates from Finnhub
for each active watchlist ticker and upserts them into the ``earnings``
table keyed by ``(symbol, earnings_date)``. Re-running on the same window
is idempotent and refreshes ``time_of_day`` if Finnhub revised it.

We issue one call per symbol rather than a single bulk-calendar fetch:
the bulk endpoint silently omits some upcoming reports (observed: MSTR
2026-05-05 missing from bulk but present when queried by symbol), while
per-symbol queries return the full schedule. With the 60 cpm free-tier
limit and our small watchlist this is well within budget.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

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


def fetch_earnings(
    session: Session,
    client: EarningsSource,
    symbols: list[str] | None = None,
    *,
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
    as_of: date | None = None,
) -> EarningsFetchSummary:
    """Fetch + persist earnings for active tickers over the next ``lookahead_days``."""
    target = _resolve_symbols(session, symbols)
    today = as_of or _today_utc()
    to_date = today + timedelta(days=lookahead_days)

    collected: list[EarningsRecord] = []
    for sym in target:
        records = client.get_earnings_calendar(from_date=today, to_date=to_date, symbol=sym)
        collected.extend(r for r in records if r.symbol == sym)

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
    )
    return EarningsFetchSummary(
        symbols_in_window=in_window,
        rows_written=written,
        window_from=today,
        window_to=to_date,
    )


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
