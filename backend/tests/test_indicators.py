"""Tests for indicator computation.

Mix of: shape/contract checks, hand-verified values on a tiny known series,
and snapshot tests on the synthetic 5-year fixture for regression detection.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from syrupy.assertion import SnapshotAssertion

from ingestion.indicators import INDICATOR_COLUMNS, compute_indicators
from tests.fixtures.bars import synth_bars


def test_returns_expected_columns() -> None:
    bars = synth_bars(days=300)
    out = compute_indicators(bars)
    assert tuple(out.columns) == INDICATOR_COLUMNS
    assert len(out) == len(bars)
    assert (out.index == bars.index).all()


def test_rejects_missing_columns() -> None:
    bars = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]},
        index=pd.DatetimeIndex([date(2024, 1, 2)]),
    )
    with pytest.raises(ValueError, match="missing columns"):
        compute_indicators(bars)


def test_rejects_non_monotonic_index() -> None:
    bars = synth_bars(days=10)
    bars = bars.iloc[::-1]
    with pytest.raises(ValueError, match="monotonic"):
        compute_indicators(bars)


def test_ema_warmup_then_populated() -> None:
    bars = synth_bars(days=300)
    out = compute_indicators(bars)

    assert pd.isna(out["ema_200"].iloc[100])
    assert not pd.isna(out["ema_200"].iloc[-1])
    assert not pd.isna(out["ema_20"].iloc[-1])
    assert not pd.isna(out["ema_50"].iloc[-1])


def test_weekly_ema_200_requires_long_history() -> None:
    short = synth_bars(days=300)
    long_bars = synth_bars(days=252 * 5)

    out_short = compute_indicators(short)
    out_long = compute_indicators(long_bars)

    assert out_short["ema_200_weekly"].isna().all()
    assert not pd.isna(out_long["ema_200_weekly"].iloc[-1])


def test_rsi_in_unit_range() -> None:
    bars = synth_bars(days=300)
    rsi = compute_indicators(bars)["rsi_14"].dropna()
    assert ((rsi >= 0) & (rsi <= 100)).all()


def test_bb_ordering() -> None:
    bars = synth_bars(days=300)
    out = compute_indicators(bars).dropna(subset=["bb_upper", "bb_lower", "bb_mid"])
    assert (out["bb_upper"] >= out["bb_mid"]).all()
    assert (out["bb_mid"] >= out["bb_lower"]).all()


def test_constant_series_rsi_undefined_atr_zero() -> None:
    idx = pd.DatetimeIndex(pd.bdate_range("2024-01-01", periods=100), name="date")
    bars = pd.DataFrame(
        {
            "open": np.full(100, 100.0),
            "high": np.full(100, 100.0),
            "low": np.full(100, 100.0),
            "close": np.full(100, 100.0),
            "volume": np.full(100, 1_000_000, dtype=int),
        },
        index=idx,
    )
    out = compute_indicators(bars)

    assert (out["atr_14"].dropna() == 0).all()
    assert (out["ema_20"].dropna() == 100.0).all()
    assert (out["hv_20"].dropna() == 0).all()


def test_snapshot_last_30_rows(snapshot: SnapshotAssertion) -> None:
    """Pin the indicator output for the synthetic fixture to detect regressions.

    To intentionally update: ``pytest --snapshot-update``.
    """
    bars = synth_bars()
    out = compute_indicators(bars).round(6)
    tail = out.tail(30)
    rows = [
        {
            "date": _to_date_str(idx),
            **{str(c): _coerce(v) for c, v in row.items()},
        }
        for idx, row in tail.iterrows()
    ]
    assert rows == snapshot


def _to_date_str(idx: object) -> str:
    if isinstance(idx, pd.Timestamp):
        return idx.date().isoformat()
    return str(idx)


def _coerce(value: object) -> object:
    if isinstance(value, float) and np.isnan(value):
        return None
    return value
