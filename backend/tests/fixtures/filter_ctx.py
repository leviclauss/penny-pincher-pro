"""Helpers to build a ``FilterContext`` in tests without standing up a DB.

Filters are pure functions of their context, so for unit tests we want to
construct contexts directly from synthetic bars + an indicator row + an
optional options chain. Phase-2 filter tests will use ``make_context`` to
hand-craft the inputs that exercise each threshold.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from db.models.market import Ticker
from screener.filters.base import FilterContext
from screener.loader import INDICATOR_COLUMNS


def make_ticker(
    symbol: str = "TEST",
    *,
    sector: str | None = "Technology",
    market_cap: float | None = 50_000_000_000.0,
    tier: int | None = 1,
) -> Ticker:
    """Build an unpersisted ``Ticker`` row for tests."""
    return Ticker(
        symbol=symbol,
        name=f"{symbol} Inc.",
        sector=sector,
        industry=None,
        market_cap=market_cap,
        is_active=True,
        tier=tier,
    )


def make_indicators(as_of: date, **values: float | None) -> pd.Series:
    """Build an indicator row; unspecified columns default to NaN."""
    data: dict[str, float | None] = dict.fromkeys(INDICATOR_COLUMNS)
    for key, value in values.items():
        if key not in INDICATOR_COLUMNS:
            raise KeyError(f"unknown indicator column: {key!r}")
        data[key] = value
    return pd.Series(data, name=as_of)


def make_context(
    *,
    symbol: str = "TEST",
    as_of: date = date(2026, 5, 1),
    bars: pd.DataFrame | None = None,
    indicators: pd.Series | None = None,
    options_chain: pd.DataFrame | None = None,
    earnings: list[date] | None = None,
    ticker: Ticker | None = None,
) -> FilterContext:
    """Construct a ``FilterContext`` from optional pieces, defaulting to empty."""
    if bars is None:
        empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        empty.index = pd.DatetimeIndex([], name="date")
        bars = empty
    if indicators is None:
        indicators = make_indicators(as_of)
    return FilterContext(
        symbol=symbol,
        as_of=as_of,
        bars=bars,
        indicators=indicators,
        options_chain=options_chain,
        earnings=list(earnings) if earnings is not None else [],
        ticker=ticker if ticker is not None else make_ticker(symbol),
    )
