"""Forward-return helpers for backtesting and screener evaluation.

Two modes:
1. ``compute_forward_return`` / ``compute_forward_return_detail`` — simple
   per-symbol, per-date forward return used by the filter-backtest engine.
2. ``evaluate_forward_returns`` — aggregate evaluator that computes 5/10/21-day
   forward returns for all passed screener results in a date range.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backtest.stats import hit_rate, safe_mean, safe_median
from core.logging import get_logger
from db.models.market import BarDaily
from db.models.screener import FilterConfig, ScreenerResult

log = get_logger(__name__)

HOLDING_PERIODS = (5, 10, 21)


# ---------------------------------------------------------------------------
# Simple per-symbol forward return (used by filter_backtest)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Aggregate forward-return evaluator for screener picks
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ForwardReturnRow:
    """One screener hit with its forward returns."""

    symbol: str
    date: date
    score: float | None
    close_on_date: float | None
    return_5d: float | None
    return_10d: float | None
    return_21d: float | None


@dataclass(frozen=True, slots=True)
class ForwardReturnSummary:
    """Aggregate statistics for a set of forward returns."""

    config_id: int
    config_name: str
    start_date: date
    end_date: date
    total_picks: int
    picks_with_returns: int

    hit_rate_5d: float | None
    hit_rate_10d: float | None
    hit_rate_21d: float | None
    mean_return_5d: float | None
    mean_return_10d: float | None
    mean_return_21d: float | None
    median_return_5d: float | None
    median_return_10d: float | None
    median_return_21d: float | None

    rows: list[ForwardReturnRow]


def _get_close_on_date(session: Session, symbol: str, on_date: date) -> float | None:
    """Fetch the close price for a symbol on a specific date."""
    stmt = select(BarDaily.close).where(
        BarDaily.symbol == symbol,
        BarDaily.date == on_date,
    )
    return session.execute(stmt).scalar_one_or_none()


def _get_forward_close(
    session: Session, symbol: str, after_date: date, n_days: int
) -> float | None:
    """Get the close price N trading days after a given date."""
    stmt = (
        select(BarDaily.close)
        .where(BarDaily.symbol == symbol, BarDaily.date > after_date)
        .order_by(BarDaily.date.asc())
        .limit(n_days)
    )
    rows = session.execute(stmt).scalars().all()
    if len(rows) < n_days:
        return None
    return rows[-1]


def _compute_return(entry_close: float | None, future_close: float | None) -> float | None:
    """Compute simple return, guarding against None or zero entry."""
    if entry_close is None or future_close is None or entry_close == 0.0:
        return None
    return round((future_close - entry_close) / entry_close, 6)


def evaluate_forward_returns(
    session: Session,
    *,
    config_id: int,
    start_date: date,
    end_date: date,
    holding_periods: tuple[int, ...] = HOLDING_PERIODS,
) -> ForwardReturnSummary:
    """Compute forward returns for all passed screener results in the date range."""
    config = session.execute(
        select(FilterConfig).where(FilterConfig.id == config_id)
    ).scalar_one_or_none()
    if config is None:
        raise ValueError(f"FilterConfig with id={config_id} not found")

    log.info(
        "forward_returns.start",
        config_id=config_id,
        config_name=config.name,
        start_date=str(start_date),
        end_date=str(end_date),
    )

    stmt = (
        select(ScreenerResult)
        .where(
            ScreenerResult.config_id == config_id,
            ScreenerResult.passed.is_(True),
            ScreenerResult.date >= start_date,
            ScreenerResult.date <= end_date,
        )
        .order_by(ScreenerResult.date, ScreenerResult.symbol)
    )
    results = session.execute(stmt).scalars().all()

    rows: list[ForwardReturnRow] = []
    for result in results:
        entry_close = _get_close_on_date(session, result.symbol, result.date)

        returns: dict[int, float | None] = {}
        for period in holding_periods:
            future_close = _get_forward_close(session, result.symbol, result.date, period)
            returns[period] = _compute_return(entry_close, future_close)

        row = ForwardReturnRow(
            symbol=result.symbol,
            date=result.date,
            score=result.score,
            close_on_date=entry_close,
            return_5d=returns.get(5),
            return_10d=returns.get(10),
            return_21d=returns.get(21),
        )
        rows.append(row)

    picks_with_returns = sum(
        1 for r in rows if any(
            v is not None for v in (r.return_5d, r.return_10d, r.return_21d)
        )
    )

    returns_5d = [r.return_5d for r in rows if r.return_5d is not None]
    returns_10d = [r.return_10d for r in rows if r.return_10d is not None]
    returns_21d = [r.return_21d for r in rows if r.return_21d is not None]

    summary = ForwardReturnSummary(
        config_id=config_id,
        config_name=config.name,
        start_date=start_date,
        end_date=end_date,
        total_picks=len(rows),
        picks_with_returns=picks_with_returns,
        hit_rate_5d=hit_rate(returns_5d),
        hit_rate_10d=hit_rate(returns_10d),
        hit_rate_21d=hit_rate(returns_21d),
        mean_return_5d=safe_mean(returns_5d),
        mean_return_10d=safe_mean(returns_10d),
        mean_return_21d=safe_mean(returns_21d),
        median_return_5d=safe_median(returns_5d),
        median_return_10d=safe_median(returns_10d),
        median_return_21d=safe_median(returns_21d),
        rows=rows,
    )

    log.info(
        "forward_returns.done",
        config_id=config_id,
        total_picks=summary.total_picks,
        picks_with_returns=summary.picks_with_returns,
    )

    return summary
