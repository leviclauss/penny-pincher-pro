"""Build a ``FilterContext`` for ``(symbol, as_of)`` from the database.

This is the single read path the screener pipeline uses to evaluate filters.
Bars and indicators are sliced point-in-time so the same loader works for
nightly screening and for backtests over historical dates. The options
chain is the *current* snapshot regardless of ``as_of`` — see CLAUDE.md /
docs 06; backtests must not rely on it.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models.market import BarDaily, Earnings, IndicatorDaily, OptionsSnapshot, Ticker
from screener.filters.base import FilterContext

INDICATOR_COLUMNS: tuple[str, ...] = (
    "ema_20",
    "ema_50",
    "ema_200",
    "ema_200_weekly",
    "rsi_14",
    "atr_14",
    "bb_upper",
    "bb_lower",
    "bb_mid",
    "iv_atm",
    "iv_rank",
    "iv_percentile",
    "hv_20",
)

OPTIONS_COLUMNS: tuple[str, ...] = (
    "expiration",
    "strike",
    "option_type",
    "bid",
    "ask",
    "last",
    "volume",
    "open_interest",
    "delta",
    "gamma",
    "theta",
    "vega",
    "iv",
)


class TickerNotFoundError(LookupError):
    """Raised when ``build_context`` is called for a symbol with no ``tickers`` row."""


def build_context(session: Session, symbol: str, as_of: date) -> FilterContext:
    """Assemble a ``FilterContext`` for one ticker on ``as_of``."""
    ticker = session.get(Ticker, symbol)
    if ticker is None:
        raise TickerNotFoundError(f"no tickers row for {symbol!r}")

    bars = _load_bars(session, symbol, as_of)
    indicators = _load_indicators(session, symbol, as_of)
    options_chain = _load_options_chain(session, symbol)
    earnings = _load_upcoming_earnings(session, symbol, as_of)

    return FilterContext(
        symbol=symbol,
        as_of=as_of,
        bars=bars,
        indicators=indicators,
        options_chain=options_chain,
        earnings=earnings,
        ticker=ticker,
    )


def _load_bars(session: Session, symbol: str, as_of: date) -> pd.DataFrame:
    rows = session.execute(
        select(
            BarDaily.date,
            BarDaily.open,
            BarDaily.high,
            BarDaily.low,
            BarDaily.close,
            BarDaily.volume,
        )
        .where(BarDaily.symbol == symbol)
        .where(BarDaily.date <= as_of)
        .order_by(BarDaily.date)
    ).all()
    if not rows:
        empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        empty.index = pd.DatetimeIndex([], name="date")
        return empty
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df.index = pd.DatetimeIndex(df["date"], name="date")
    return df.drop(columns=["date"])


def _load_indicators(session: Session, symbol: str, as_of: date) -> pd.Series:
    row = session.execute(
        select(IndicatorDaily)
        .where(IndicatorDaily.symbol == symbol)
        .where(IndicatorDaily.date == as_of)
    ).scalar_one_or_none()

    if row is None:
        return pd.Series({col: float("nan") for col in INDICATOR_COLUMNS}, name=as_of)

    data = {col: getattr(row, col) for col in INDICATOR_COLUMNS}
    return pd.Series(data, name=as_of)


def _load_options_chain(session: Session, symbol: str) -> pd.DataFrame | None:
    rows = (
        session.execute(select(OptionsSnapshot).where(OptionsSnapshot.symbol == symbol))
        .scalars()
        .all()
    )
    if not rows:
        return None
    return pd.DataFrame(
        [{col: getattr(r, col) for col in OPTIONS_COLUMNS} for r in rows],
        columns=list(OPTIONS_COLUMNS),
    )


def _load_upcoming_earnings(session: Session, symbol: str, as_of: date) -> list[date]:
    rows = session.execute(
        select(Earnings.earnings_date)
        .where(Earnings.symbol == symbol)
        .where(Earnings.earnings_date >= as_of)
        .order_by(Earnings.earnings_date)
    ).all()
    return [r[0] for r in rows]


__all__ = ["INDICATOR_COLUMNS", "OPTIONS_COLUMNS", "TickerNotFoundError", "build_context"]
