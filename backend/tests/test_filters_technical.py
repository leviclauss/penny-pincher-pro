"""Tier 1 filter unit tests."""

from __future__ import annotations

import pandas as pd
import pytest

from screener.filters import technical
from tests.fixtures.contexts import constant_bars, make_context

# near_200ema -----------------------------------------------------------------


def test_near_200ema_passes_within_threshold() -> None:
    ctx = make_context(bars=constant_bars(102.0), indicators={"ema_200": 100.0})
    r = technical.Near200EMA().evaluate(ctx, {"max_pct": 0.03})
    assert r.passed is True
    assert r.eligible is True
    assert r.value == pytest.approx(0.02)
    assert r.score is not None and 0.0 < r.score <= 1.0


def test_near_200ema_fails_outside_threshold() -> None:
    ctx = make_context(bars=constant_bars(110.0), indicators={"ema_200": 100.0})
    r = technical.Near200EMA().evaluate(ctx, {"max_pct": 0.03})
    assert r.passed is False
    assert r.value == pytest.approx(0.10)
    assert r.score == 0.0


def test_near_200ema_ineligible_when_ema_missing() -> None:
    ctx = make_context(bars=constant_bars(100.0), indicators={"ema_200": None})
    r = technical.Near200EMA().evaluate(ctx, {})
    assert r.eligible is False
    assert r.passed is False


def test_near_200ema_uses_default_threshold() -> None:
    # Default = 3%; 102 vs 100 → 2% → passes.
    ctx = make_context(bars=constant_bars(102.0), indicators={"ema_200": 100.0})
    assert technical.Near200EMA().evaluate(ctx, {}).passed is True


def test_near_200ema_at_exact_threshold_passes() -> None:
    ctx = make_context(bars=constant_bars(103.0), indicators={"ema_200": 100.0})
    r = technical.Near200EMA().evaluate(ctx, {"max_pct": 0.03})
    assert r.passed is True


# near_50ema -----------------------------------------------------------------


def test_near_50ema_default_threshold_two_pct() -> None:
    # 101 vs 100 → 1% → passes default 2%.
    ctx = make_context(bars=constant_bars(101.0), indicators={"ema_50": 100.0})
    assert technical.Near50EMA().evaluate(ctx, {}).passed is True
    # 105 → 5% → fails default 2%.
    ctx2 = make_context(bars=constant_bars(105.0), indicators={"ema_50": 100.0})
    assert technical.Near50EMA().evaluate(ctx2, {}).passed is False


# weekly_above_200ema --------------------------------------------------------


def test_weekly_above_200ema_warmup_is_ineligible() -> None:
    ctx = make_context(bars=constant_bars(100.0), indicators={"ema_200_weekly": None})
    r = technical.WeeklyAbove200EMA().evaluate(ctx, {})
    assert r.eligible is False
    assert r.reason == "weekly_ema_200_warmup"


def test_weekly_above_200ema_pass_when_above() -> None:
    ctx = make_context(bars=constant_bars(110.0), indicators={"ema_200_weekly": 100.0})
    r = technical.WeeklyAbove200EMA().evaluate(ctx, {})
    assert r.passed is True


def test_weekly_above_200ema_fail_when_below() -> None:
    ctx = make_context(bars=constant_bars(90.0), indicators={"ema_200_weekly": 100.0})
    r = technical.WeeklyAbove200EMA().evaluate(ctx, {})
    assert r.passed is False


# rsi_oversold --------------------------------------------------------------


def test_rsi_oversold_passes_below_threshold() -> None:
    ctx = make_context(bars=constant_bars(100.0), indicators={"rsi_14": 25.0})
    r = technical.RsiOversold().evaluate(ctx, {"max_rsi": 35.0})
    assert r.passed is True
    assert r.value == 25.0
    assert r.score is not None and r.score > 0


def test_rsi_oversold_fails_at_or_above_threshold() -> None:
    ctx = make_context(bars=constant_bars(100.0), indicators={"rsi_14": 35.0})
    r = technical.RsiOversold().evaluate(ctx, {"max_rsi": 35.0})
    assert r.passed is False  # strict <


def test_rsi_oversold_ineligible_without_rsi() -> None:
    ctx = make_context(bars=constant_bars(100.0), indicators={"rsi_14": None})
    r = technical.RsiOversold().evaluate(ctx, {})
    assert r.eligible is False


# bb_lower_touch ------------------------------------------------------------


def test_bb_lower_touch_pass_at_or_below_band() -> None:
    ctx = make_context(bars=constant_bars(95.0), indicators={"bb_lower": 100.0})
    r = technical.BollingerLowerTouch().evaluate(ctx, {})
    assert r.passed is True


def test_bb_lower_touch_fail_above_band() -> None:
    ctx = make_context(bars=constant_bars(105.0), indicators={"bb_lower": 100.0})
    r = technical.BollingerLowerTouch().evaluate(ctx, {})
    assert r.passed is False


def test_bb_lower_touch_ineligible_when_band_missing() -> None:
    ctx = make_context(bars=constant_bars(95.0), indicators={"bb_lower": None})
    assert technical.BollingerLowerTouch().evaluate(ctx, {}).eligible is False


# not_freefall --------------------------------------------------------------


def _bars_with_5d_drop(start: float, end: float) -> pd.DataFrame:
    closes = [start, start, start, start, start, end]  # 6 bars: -5 lookback
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1] * len(closes),
        },
        index=pd.DatetimeIndex(pd.bdate_range("2024-01-01", periods=len(closes))),
    )


def test_not_freefall_pass_for_mild_drop() -> None:
    ctx = make_context(bars=_bars_with_5d_drop(100.0, 95.0))
    r = technical.NotFreefall().evaluate(ctx, {"min_5d_return": -0.10})
    assert r.passed is True
    assert r.value == pytest.approx(-0.05)


def test_not_freefall_fail_for_steep_drop() -> None:
    ctx = make_context(bars=_bars_with_5d_drop(100.0, 80.0))
    r = technical.NotFreefall().evaluate(ctx, {"min_5d_return": -0.10})
    assert r.passed is False
    assert r.value == pytest.approx(-0.20)


def test_not_freefall_ineligible_without_history() -> None:
    short = _bars_with_5d_drop(100.0, 95.0).iloc[:3]
    ctx = make_context(bars=short)
    r = technical.NotFreefall().evaluate(ctx, {})
    assert r.eligible is False
