"""Tier 1 — Trend / Mean Reversion filters.

All read latest close from ``ctx.bars.iloc[-1]`` and indicator values from
``ctx.indicators``. NULL indicator values during warmup (e.g. ``ema_200``
before 200 daily closes, ``ema_200_weekly`` before ~200 weeks) are mapped
to ``ineligible(...)`` so a required filter short-circuits and an optional
one drops cleanly out of scoring.

Defaults match the catalog in ``docs/planning/02-screener-filters.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

import pandas as pd

from screener.filters.base import FilterContext, FilterResult, ineligible

NEAR_200EMA_DEFAULT_MAX_PCT = 0.03
NEAR_50EMA_DEFAULT_MAX_PCT = 0.02
RSI_OVERSOLD_DEFAULT_MAX = 35.0
NOT_FREEFALL_DEFAULT_MIN_5D_RETURN = -0.10
NOT_FREEFALL_LOOKBACK_BARS = 6  # 5-day return = close[-1] / close[-6] - 1


def _indicator(ctx: FilterContext, name: str) -> float | None:
    if ctx.indicators is None:
        return None
    value = ctx.indicators.get(name)
    if value is None or pd.isna(value):
        return None
    return float(value)


def _proximity_score(distance: float, threshold: float) -> float:
    """Map distance ``0..threshold`` linearly to score ``1..0``."""
    if threshold <= 0:
        return 1.0 if distance <= 0 else 0.0
    return max(0.0, 1.0 - distance / threshold)


class Near200EMA:
    """Close within ``max_pct`` of the 200-day EMA (above or below)."""

    id: ClassVar[str] = "near_200ema"

    def evaluate(self, ctx: FilterContext, params: Mapping[str, Any]) -> FilterResult:
        max_pct = float(params.get("max_pct", NEAR_200EMA_DEFAULT_MAX_PCT))
        close = ctx.latest_close()
        ema = _indicator(ctx, "ema_200")
        if close is None or ema is None or ema == 0:
            return ineligible("missing_close_or_ema_200")
        distance = abs(close - ema) / ema
        passed = distance <= max_pct
        return FilterResult(
            passed=passed,
            score=_proximity_score(distance, max_pct) if passed else 0.0,
            value=distance,
        )


class Near50EMA:
    """Close within ``max_pct`` of the 50-day EMA."""

    id: ClassVar[str] = "near_50ema"

    def evaluate(self, ctx: FilterContext, params: Mapping[str, Any]) -> FilterResult:
        max_pct = float(params.get("max_pct", NEAR_50EMA_DEFAULT_MAX_PCT))
        close = ctx.latest_close()
        ema = _indicator(ctx, "ema_50")
        if close is None or ema is None or ema == 0:
            return ineligible("missing_close_or_ema_50")
        distance = abs(close - ema) / ema
        passed = distance <= max_pct
        return FilterResult(
            passed=passed,
            score=_proximity_score(distance, max_pct) if passed else 0.0,
            value=distance,
        )


class WeeklyAbove200EMA:
    """Regime filter: weekly close > weekly EMA(200).

    The weekly series needs ~200 weeks (~4 years) of history before
    ``ema_200_weekly`` is non-NULL — see the schema notes in CLAUDE.md.
    During warmup this filter is ineligible, which means a config that
    marks it required will simply skip the symbol until enough history
    accumulates.
    """

    id: ClassVar[str] = "weekly_above_200ema"

    def evaluate(self, ctx: FilterContext, params: Mapping[str, Any]) -> FilterResult:
        del params  # no configurable threshold; >/<= weekly EMA is the test
        close = ctx.latest_close()
        weekly_ema = _indicator(ctx, "ema_200_weekly")
        if close is None:
            return ineligible("missing_close")
        if weekly_ema is None:
            return ineligible("weekly_ema_200_warmup")
        passed = close > weekly_ema
        return FilterResult(passed=passed, value=close - weekly_ema)


class RsiOversold:
    """Daily RSI(14) below ``max_rsi`` — mean-reversion entry signal."""

    id: ClassVar[str] = "rsi_oversold"

    def evaluate(self, ctx: FilterContext, params: Mapping[str, Any]) -> FilterResult:
        max_rsi = float(params.get("max_rsi", RSI_OVERSOLD_DEFAULT_MAX))
        rsi = _indicator(ctx, "rsi_14")
        if rsi is None:
            return ineligible("missing_rsi_14")
        passed = rsi < max_rsi
        # Deeper-oversold = higher score, ramped 0..max_rsi -> 1..0.
        score = max(0.0, 1.0 - rsi / max_rsi) if passed and max_rsi > 0 else 0.0
        return FilterResult(passed=passed, score=score, value=rsi)


class BollingerLowerTouch:
    """Close at or below the lower Bollinger Band (mean-reversion entry)."""

    id: ClassVar[str] = "bb_lower_touch"

    def evaluate(self, ctx: FilterContext, params: Mapping[str, Any]) -> FilterResult:
        del params
        close = ctx.latest_close()
        bb_lower = _indicator(ctx, "bb_lower")
        if close is None or bb_lower is None:
            return ineligible("missing_close_or_bb_lower")
        passed = close <= bb_lower
        return FilterResult(passed=passed, value=close - bb_lower)


class NotFreefall:
    """5-day return above ``min_5d_return`` (default -10%) — anti knife-catch."""

    id: ClassVar[str] = "not_freefall"

    def evaluate(self, ctx: FilterContext, params: Mapping[str, Any]) -> FilterResult:
        min_return = float(params.get("min_5d_return", NOT_FREEFALL_DEFAULT_MIN_5D_RETURN))
        if len(ctx.bars) < NOT_FREEFALL_LOOKBACK_BARS:
            return ineligible("insufficient_bars_for_5d_return")
        close = float(ctx.bars["close"].iloc[-1])
        prior = float(ctx.bars["close"].iloc[-NOT_FREEFALL_LOOKBACK_BARS])
        if prior == 0:
            return ineligible("zero_prior_close")
        ret_5d = close / prior - 1.0
        passed = ret_5d > min_return
        return FilterResult(passed=passed, value=ret_5d)
