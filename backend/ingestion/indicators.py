"""Technical indicator computation.

Pure functions over a daily-bar DataFrame. No I/O, no DB, no SDK calls — this
module is what the snapshot tests pin down. Indicator definitions:

- EMA 20 / 50 / 200: exponential moving average of close, daily.
- EMA 200 weekly: EMA(200) of the last close in each W-FRI week, forward-filled
  to daily index. Returns NaN until ~200 weeks of history accumulate.
- RSI(14): standard Wilder.
- ATR(14): standard Wilder.
- Bollinger Bands: 20-period SMA ± 2·std.
- HV(20): annualized stdev of 20-day log returns (sqrt(252) scaling).

IV-derived indicators (iv_atm, iv_rank, iv_percentile) are populated by the
options ingestion in a later session and remain NaN here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands

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
    "hv_20",
)

TRADING_DAYS_PER_YEAR = 252


def compute_indicators(bars: pd.DataFrame) -> pd.DataFrame:
    """Compute all indicators for a single symbol.

    ``bars`` must be indexed by ``date`` (ascending) with columns
    ``open, high, low, close, volume``. Returns a DataFrame with the same
    index and columns from ``INDICATOR_COLUMNS``.
    """
    _validate_bars(bars)

    close = bars["close"].astype(float)
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)

    out = pd.DataFrame(index=bars.index)

    out["ema_20"] = EMAIndicator(close=close, window=20, fillna=False).ema_indicator()
    out["ema_50"] = EMAIndicator(close=close, window=50, fillna=False).ema_indicator()
    out["ema_200"] = EMAIndicator(close=close, window=200, fillna=False).ema_indicator()
    out["ema_200_weekly"] = _weekly_ema_200(close)

    out["rsi_14"] = RSIIndicator(close=close, window=14, fillna=False).rsi()
    out["atr_14"] = AverageTrueRange(
        high=high, low=low, close=close, window=14, fillna=False
    ).average_true_range()

    bb = BollingerBands(close=close, window=20, window_dev=2, fillna=False)
    out["bb_upper"] = bb.bollinger_hband()
    out["bb_lower"] = bb.bollinger_lband()
    out["bb_mid"] = bb.bollinger_mavg()

    out["hv_20"] = _historical_volatility(close, window=20)

    return out[list(INDICATOR_COLUMNS)]


def _validate_bars(bars: pd.DataFrame) -> None:
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"bars is missing columns: {sorted(missing)}")
    if not bars.index.is_monotonic_increasing:
        raise ValueError("bars index must be monotonically increasing")


def _weekly_ema_200(daily_close: pd.Series) -> pd.Series:
    """EMA(200) over weekly closes, broadcast back to the daily index.

    Uses W-FRI (NYSE week-ending) weekly bars. The most recent partial week
    is included via ``last()`` so the series is current to the latest daily bar.
    """
    weekly = daily_close.resample("W-FRI").last().dropna()
    weekly_ema = weekly.ewm(span=200, adjust=False, min_periods=200).mean()
    return weekly_ema.reindex(daily_close.index, method="ffill")


def _historical_volatility(close: pd.Series, window: int) -> pd.Series:
    log_close = pd.Series(np.log(close.to_numpy()), index=close.index)
    log_returns = log_close.diff()
    rolling_std: pd.Series = log_returns.rolling(window=window, min_periods=window).std()
    annualized: pd.Series = rolling_std * float(np.sqrt(TRADING_DAYS_PER_YEAR))
    return annualized
