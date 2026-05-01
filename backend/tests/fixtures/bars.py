"""Deterministic synthetic bar fixtures for indicator tests.

We synthesize rather than vendor real market data so:
- the fixture is reproducible across machines and CI
- the test suite has no network dependency
- regenerating doesn't introduce silent drift (a fixed seed pins the values)

The series is a geometric random walk with mild drift, then OHLC is synthesized
around each close. Five years of trading days gives weekly EMA 200 enough
history to produce non-null values.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

DEFAULT_SEED = 20260501
DEFAULT_START = date(2020, 1, 2)
DEFAULT_DAYS = 252 * 5
DEFAULT_START_PRICE = 100.0


def synth_bars(
    *,
    seed: int = DEFAULT_SEED,
    start: date = DEFAULT_START,
    days: int = DEFAULT_DAYS,
    start_price: float = DEFAULT_START_PRICE,
) -> pd.DataFrame:
    """Build a deterministic OHLCV DataFrame indexed by trading-day dates."""
    rng = np.random.default_rng(seed)

    daily_returns = rng.normal(loc=0.0003, scale=0.012, size=days)
    closes = start_price * np.exp(np.cumsum(daily_returns))

    intra_range = np.abs(rng.normal(loc=0.0, scale=0.008, size=days))
    highs = closes * (1.0 + intra_range)
    lows = closes * (1.0 - intra_range)
    opens = np.empty_like(closes)
    opens[0] = start_price
    opens[1:] = closes[:-1]

    volumes = rng.integers(low=500_000, high=5_000_000, size=days)

    index = pd.DatetimeIndex(_business_days(start, days), name="date")
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        },
        index=index,
    )


def _business_days(start: date, count: int) -> list[date]:
    days: list[date] = []
    cur = start
    while len(days) < count:
        if cur.weekday() < 5:
            days.append(cur)
        cur = cur + timedelta(days=1)
    return days
