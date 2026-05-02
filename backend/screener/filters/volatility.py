"""Tier 2 — Volatility / Premium filters.

All depend on IV columns of ``indicators_daily``. Per CLAUDE.md, ``iv_rank``
and ``iv_percentile`` need a 252-day rolling window and remain NULL until
≥126 days of valid ``iv_atm`` history accumulate (no backfill — Alpaca's
options history is shallow). NULL inputs map to ``ineligible(...)``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

import pandas as pd

from screener.filters.base import FilterContext, FilterResult, ineligible

IV_RANK_DEFAULT_MIN = 50.0
IV_PERCENTILE_DEFAULT_MIN = 50.0
IV_ABOVE_HV_DEFAULT_RATIO = 1.2


def _indicator(ctx: FilterContext, name: str) -> float | None:
    if ctx.indicators is None:
        return None
    value = ctx.indicators.get(name)
    if value is None or pd.isna(value):
        return None
    return float(value)


class IvRankHigh:
    """IV Rank ≥ ``min`` (rank is on a 0..100 scale)."""

    id: ClassVar[str] = "iv_rank_high"

    def evaluate(self, ctx: FilterContext, params: Mapping[str, Any]) -> FilterResult:
        min_rank = float(params.get("min", IV_RANK_DEFAULT_MIN))
        iv_rank = _indicator(ctx, "iv_rank")
        if iv_rank is None:
            return ineligible("iv_rank_warmup")
        passed = iv_rank >= min_rank
        score = max(0.0, min(1.0, iv_rank / 100.0)) if passed else 0.0
        return FilterResult(passed=passed, score=score, value=iv_rank)


class IvPercentileHigh:
    """IV Percentile ≥ ``min`` (percentile is on a 0..100 scale)."""

    id: ClassVar[str] = "iv_percentile_high"

    def evaluate(self, ctx: FilterContext, params: Mapping[str, Any]) -> FilterResult:
        min_pct = float(params.get("min", IV_PERCENTILE_DEFAULT_MIN))
        iv_pct = _indicator(ctx, "iv_percentile")
        if iv_pct is None:
            return ineligible("iv_percentile_warmup")
        passed = iv_pct >= min_pct
        score = max(0.0, min(1.0, iv_pct / 100.0)) if passed else 0.0
        return FilterResult(passed=passed, score=score, value=iv_pct)


class IvAboveHv:
    """ATM IV / HV(20) ≥ ``min_ratio`` — premium-rich proxy."""

    id: ClassVar[str] = "iv_above_hv"

    def evaluate(self, ctx: FilterContext, params: Mapping[str, Any]) -> FilterResult:
        min_ratio = float(params.get("min_ratio", IV_ABOVE_HV_DEFAULT_RATIO))
        iv = _indicator(ctx, "iv_atm")
        hv = _indicator(ctx, "hv_20")
        if iv is None:
            return ineligible("missing_iv_atm")
        if hv is None or hv == 0:
            return ineligible("missing_or_zero_hv_20")
        ratio = iv / hv
        passed = ratio >= min_ratio
        return FilterResult(passed=passed, value=ratio)
