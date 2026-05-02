"""Build a point-in-time ``FilterContext`` for one (symbol, as_of) pair.

This is the only place the screener pipeline talks to the DB. Every query is
date-filtered so filters can trust their inputs without re-checking. Options
are loaded only when ``as_of == today`` because ``options_snapshot`` is a
current-only table — backtest runs always pass ``include_options=False``.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.time import market_today
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


def build_context(
    session: Session,
    symbol: str,
    as_of: date,
    *,
    ticker: Ticker | None = None,
    include_options: bool | None = None,
) -> FilterContext | None:
    """Assemble inputs for ``(symbol, as_of)``.

    Returns ``None`` if the ticker isn't in the watchlist — callers iterate
    only over known symbols, so this should be rare.

    ``include_options`` defaults to ``as_of == market_today()`` because the
    snapshot table is current-only; backtests pass ``False`` explicitly.
    """
    if ticker is None:
        ticker = session.get(Ticker, symbol)
        if ticker is None:
            return None

    bars = _load_bars_through(session, symbol, as_of)
    indicators = _load_indicator_row(session, symbol, as_of)
    earnings = _load_future_earnings(session, symbol, as_of)
    macro = _load_macro_row(session, as_of)

    if include_options is None:
        include_options = as_of >= market_today()
    options_chain = _load_options_chain(session, symbol) if include_options else None

    return FilterContext(
        symbol=symbol,
        as_of=as_of,
        bars=bars,
        indicators=indicators,
        options_chain=options_chain or None,
        earnings=earnings,
        ticker=ticker,
        macro=macro,
    )


def _load_bars_through(session: Session, symbol: str, as_of: date) -> pd.DataFrame:
    rows = session.execute(
        select(
            BarDaily.date,
            BarDaily.open,
            BarDaily.high,
            BarDaily.low,
            BarDaily.close,
            BarDaily.volume,
        )
        .where(BarDaily.symbol == symbol, BarDaily.date <= as_of)
        .order_by(BarDaily.date)
    ).all()
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df.index = pd.DatetimeIndex(df["date"])
    df.index.name = "date"
    return df.drop(columns=["date"])


def _load_indicator_row(session: Session, symbol: str, as_of: date) -> pd.Series | None:
    """Latest indicator row at-or-before ``as_of`` — typically same day."""
    row = session.execute(
        select(IndicatorDaily)
        .where(IndicatorDaily.symbol == symbol, IndicatorDaily.date <= as_of)
        .order_by(IndicatorDaily.date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    return pd.Series(
        {
            "ema_20": row.ema_20,
            "ema_50": row.ema_50,
            "ema_200": row.ema_200,
            "ema_200_weekly": row.ema_200_weekly,
            "rsi_14": row.rsi_14,
            "atr_14": row.atr_14,
            "bb_upper": row.bb_upper,
            "bb_lower": row.bb_lower,
            "bb_mid": row.bb_mid,
            "iv_atm": row.iv_atm,
            "iv_rank": row.iv_rank,
            "iv_percentile": row.iv_percentile,
            "hv_20": row.hv_20,
        }
    )


def _load_future_earnings(session: Session, symbol: str, as_of: date) -> list[date]:
    rows = (
        session.execute(
            select(Earnings.earnings_date)
            .where(Earnings.symbol == symbol, Earnings.earnings_date >= as_of)
            .order_by(Earnings.earnings_date)
        )
        .scalars()
        .all()
    )
    return list(rows)


def _load_options_chain(session: Session, symbol: str) -> list[OptionSnapshotRecord]:
    """Read the current ``options_snapshot`` rows for ``symbol`` as records.

    Inlined here (rather than reused from ``ingestion.persistence``) so the
    screener track doesn't transitively depend on the ingestion-only
    indicator libraries.
    """
    rows = (
        session.execute(select(OptionsSnapshot).where(OptionsSnapshot.symbol == symbol))
        .scalars()
        .all()
    )
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


def _load_macro_row(session: Session, as_of: date) -> pd.Series | None:
    row = session.execute(
        select(MacroDaily).where(MacroDaily.date <= as_of).order_by(MacroDaily.date.desc()).limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    return pd.Series(
        {
            "vix_close": row.vix_close,
            "vix_9d": row.vix_9d,
            "vix_term_structure": row.vix_term_structure,
            "spy_close": row.spy_close,
            "spy_ema_200": row.spy_ema_200,
            "spy_above_200ema": row.spy_above_200ema,
        }
    )
