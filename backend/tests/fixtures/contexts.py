"""Helpers for constructing ``FilterContext`` and option records in tests.

Filter unit tests should not require a DB session — they get a hand-built
context with just the inputs the filter under test reads. ``make_context``
fills in safe defaults (empty bars, no indicators, no chain) so each test
only specifies what matters.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import pandas as pd

from db.models.market import Ticker
from ingestion.options_client import OptionSnapshotRecord
from screener.filters.base import FilterContext

_DEFAULT_AS_OF = date(2024, 6, 3)


def make_ticker(
    symbol: str = "TEST",
    *,
    sector: str | None = "Technology",
    market_cap: float | None = 50_000_000_000.0,
    tier: int | None = 1,
) -> Ticker:
    return Ticker(
        symbol=symbol,
        name=f"{symbol} Inc.",
        sector=sector,
        market_cap=market_cap,
        tier=tier,
    )


def make_context(
    *,
    symbol: str = "TEST",
    as_of: date | None = None,
    bars: pd.DataFrame | None = None,
    indicators: Mapping[str, float | None] | None = None,
    options_chain: list[OptionSnapshotRecord] | None = None,
    earnings: list[date] | None = None,
    ticker: Ticker | None = None,
    macro: Mapping[str, float | None] | None = None,
) -> FilterContext:
    if bars is None:
        bars = pd.DataFrame({"open": [], "high": [], "low": [], "close": [], "volume": []})
    if as_of is None:
        if len(bars) > 0:
            last = bars.index[-1]
            as_of = last.date() if hasattr(last, "date") else last
        else:
            as_of = _DEFAULT_AS_OF
    return FilterContext(
        symbol=symbol,
        as_of=as_of,
        bars=bars,
        indicators=pd.Series(dict(indicators)) if indicators is not None else None,
        options_chain=options_chain,
        earnings=earnings or [],
        ticker=ticker if ticker is not None else make_ticker(symbol),
        macro=pd.Series(dict(macro)) if macro is not None else None,
    )


def make_put(
    *,
    strike: float,
    bid: float | None = 1.00,
    ask: float | None = 1.05,
    open_interest: int | None = None,
    volume: int | None = None,
    delta: float | None = None,
    expiration: date | None = None,
    symbol: str = "TEST",
) -> OptionSnapshotRecord:
    return OptionSnapshotRecord(
        symbol=symbol,
        expiration=expiration or date(2024, 7, 19),
        strike=strike,
        option_type="put",
        bid=bid,
        ask=ask,
        last=None,
        volume=volume,
        open_interest=open_interest,
        delta=delta,
        gamma=None,
        theta=None,
        vega=None,
        iv=None,
    )


def constant_bars(close: float, *, length: int = 10) -> pd.DataFrame:
    """OHLCV with constant close — useful for testing filters that only read close."""
    return pd.DataFrame(
        {
            "open": [close] * length,
            "high": [close] * length,
            "low": [close] * length,
            "close": [close] * length,
            "volume": [1_000_000] * length,
        },
        index=pd.DatetimeIndex(pd.bdate_range("2024-01-01", periods=length), name="date"),
    )
