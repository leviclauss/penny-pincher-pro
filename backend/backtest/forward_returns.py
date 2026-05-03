"""Forward-return helper for filter backtesting.

Loads enough bars after ``entry_date`` to skip weekends/holidays and returns
the percentage change between the entry-day close and the close ``forward_days``
trading days later. ``None`` whenever bars are missing — the caller treats
that symbol-day as no-data rather than a loss.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models.market import BarDaily


@dataclass(frozen=True, slots=True)
class ForwardReturn:
    entry_close: float
    exit_close: float
    exit_date: date
    pct_return: float


def compute_forward_return_detail(
    session: Session,
    symbol: str,
    entry_date: date,
    forward_days: int,
) -> ForwardReturn | None:
    end_date = entry_date + timedelta(days=2 * forward_days + 5)
    rows = session.execute(
        select(BarDaily.date, BarDaily.close)
        .where(
            BarDaily.symbol == symbol,
            BarDaily.date >= entry_date,
            BarDaily.date <= end_date,
        )
        .order_by(BarDaily.date)
    ).all()
    if not rows or rows[0][0] != entry_date or len(rows) <= forward_days:
        return None
    entry_close = float(rows[0][1])
    if entry_close == 0.0:
        return None
    exit_date, exit_close_raw = rows[forward_days]
    exit_close = float(exit_close_raw)
    return ForwardReturn(
        entry_close=entry_close,
        exit_close=exit_close,
        exit_date=exit_date,
        pct_return=(exit_close - entry_close) / entry_close,
    )


def compute_forward_return(
    session: Session,
    symbol: str,
    entry_date: date,
    forward_days: int,
) -> float | None:
    detail = compute_forward_return_detail(session, symbol, entry_date, forward_days)
    return detail.pct_return if detail is not None else None
