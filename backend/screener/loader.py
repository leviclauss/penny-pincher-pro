"""Build a ``FilterContext`` for ``(symbol, as_of)`` from the database.

Single read path the screener pipeline uses to evaluate filters. Bars,
indicators, and earnings are sliced point-in-time so the same loader works
for nightly screening and for backtests over historical dates. The options
chain and macro row are *current-only* — the underlying tables hold no
history. Backtests must not rely on either (per docs/planning/06-backtesting.md);
options-dependent filters mark themselves ineligible when ``as_of`` doesn't
match the snapshot date.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models.market import (
    BarDaily,
    Earnings,
    IndicatorDaily,
    MacroDaily,
    OptionsSnapshot,
    Ticker,
)
from ingestion.options_client import OptionSnapshotRecord
from screener.filters.base import FilterContext


class TickerNotFoundError(LookupError):
    """Raised when ``build_context`` is called for a symbol with no ``tickers`` row."""


def build_context(session: Session, symbol: str, as_of: date) -> FilterContext:
    """Assemble a ``FilterContext`` for one ticker on ``as_of``."""
    ticker = session.get(Ticker, symbol)
    if ticker is None:
        raise TickerNotFoundError(f"no tickers row for {symbol!r}")

    return FilterContext(
        symbol=symbol,
        as_of=as_of,
        bars=_load_bars(session, symbol, as_of),
        indicators=_load_indicators(session, symbol, as_of),
        options_chain=_load_options_chain(session, symbol),
        earnings=_load_upcoming_earnings(session, symbol, as_of),
        ticker=ticker,
        macro=_load_macro(session, as_of),
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


_INDICATOR_COLUMNS: tuple[str, ...] = (
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


def _load_indicators(session: Session, symbol: str, as_of: date) -> pd.Series | None:
    row = session.execute(
        select(IndicatorDaily)
        .where(IndicatorDaily.symbol == symbol)
        .where(IndicatorDaily.date == as_of)
    ).scalar_one_or_none()
    if row is None:
        return None
    return pd.Series({col: getattr(row, col) for col in _INDICATOR_COLUMNS}, name=as_of)


def _load_options_chain(session: Session, symbol: str) -> list[OptionSnapshotRecord] | None:
    rows = (
        session.execute(select(OptionsSnapshot).where(OptionsSnapshot.symbol == symbol))
        .scalars()
        .all()
    )
    if not rows:
        return None
    return [
        OptionSnapshotRecord(
            symbol=r.symbol,
            expiration=r.expiration,
            strike=r.strike,
            option_type=r.option_type,
            bid=r.bid,
            ask=r.ask,
            last=r.last,
            volume=r.volume,
            open_interest=r.open_interest,
            delta=r.delta,
            gamma=r.gamma,
            theta=r.theta,
            vega=r.vega,
            iv=r.iv,
        )
        for r in rows
    ]


def _load_upcoming_earnings(session: Session, symbol: str, as_of: date) -> list[date]:
    rows = session.execute(
        select(Earnings.earnings_date)
        .where(Earnings.symbol == symbol)
        .where(Earnings.earnings_date >= as_of)
        .order_by(Earnings.earnings_date)
    ).all()
    return [r[0] for r in rows]


_MACRO_COLUMNS: tuple[str, ...] = (
    "vix_close",
    "vix_9d",
    "vix_term_structure",
    "spy_close",
    "spy_ema_200",
    "spy_above_200ema",
)


def _load_macro(session: Session, as_of: date) -> pd.Series | None:
    row = session.execute(select(MacroDaily).where(MacroDaily.date == as_of)).scalar_one_or_none()
    if row is None:
        return None
    return pd.Series({col: getattr(row, col) for col in _MACRO_COLUMNS}, name=as_of)


__all__ = ["TickerNotFoundError", "build_context"]
