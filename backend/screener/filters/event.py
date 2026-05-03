"""Tier 4 — Event / Risk filters (single-ticker portion).

``sector_concentration`` from the catalog is intentionally NOT here: it's
cross-symbol (depends on which other tickers already passed in the same
run) and can't be expressed as a per-ticker ``Filter``. It will land in
PR3 as a ``Postprocessor`` that runs over the per-symbol results before
persistence.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any, ClassVar

from screener.filters.base import (
    FilterCategory,
    FilterContext,
    FilterResult,
    ParamSpec,
    ineligible,
)

CATEGORY: FilterCategory = "event"

NO_EARNINGS_DEFAULT_DAYS = 45
MIN_MARKET_CAP_DEFAULT_USD = 5_000_000_000.0
TIER_ALLOWED_DEFAULT: tuple[int, ...] = (1, 2)


class NoEarningsInWindow:
    """No earnings between ``as_of`` and ``as_of + days``.

    Earnings inside the option's lifetime add gap risk to the wheel —
    the catalog flags this as required by default. ``ctx.earnings`` is
    pre-filtered to dates ≥ as_of by the context builder.
    """

    id: ClassVar[str] = "no_earnings_in_window"
    label: ClassVar[str] = "No earnings in window"
    description: ClassVar[str] = (
        "No earnings between as_of and as_of + days — avoid gap risk inside the option's lifetime."
    )
    category: ClassVar[FilterCategory] = CATEGORY
    scored: ClassVar[bool] = False
    param_schema: ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec(
            name="days",
            label="Look-ahead days",
            kind="integer",
            default=NO_EARNINGS_DEFAULT_DAYS,
            min=0.0,
            max=365.0,
            step=1.0,
        ),
    )

    def evaluate(self, ctx: FilterContext, params: Mapping[str, Any]) -> FilterResult:
        days = int(params.get("days", NO_EARNINGS_DEFAULT_DAYS))
        cutoff = ctx.as_of + timedelta(days=days)
        upcoming = sorted(d for d in ctx.earnings if ctx.as_of <= d <= cutoff)
        passed = not upcoming
        return FilterResult(
            passed=passed,
            value=upcoming[0].isoformat() if upcoming else None,
        )


class MinMarketCap:
    """``ticker.market_cap`` ≥ ``min_usd``."""

    id: ClassVar[str] = "min_market_cap"
    label: ClassVar[str] = "Min market cap"
    description: ClassVar[str] = "ticker.market_cap ≥ min_usd."
    category: ClassVar[FilterCategory] = CATEGORY
    scored: ClassVar[bool] = False
    param_schema: ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec(
            name="min_usd",
            label="Min market cap (USD)",
            kind="currency",
            default=MIN_MARKET_CAP_DEFAULT_USD,
            min=0.0,
            step=1_000_000_000.0,
        ),
    )

    def evaluate(self, ctx: FilterContext, params: Mapping[str, Any]) -> FilterResult:
        min_usd = float(params.get("min_usd", MIN_MARKET_CAP_DEFAULT_USD))
        cap = ctx.ticker.market_cap
        if cap is None:
            return ineligible("missing_market_cap")
        passed = cap >= min_usd
        return FilterResult(passed=passed, value=float(cap))


class TierAllowed:
    """``ticker.tier`` is in the allowed set."""

    id: ClassVar[str] = "tier_allowed"
    label: ClassVar[str] = "Allowed tiers"
    description: ClassVar[str] = "ticker.tier is in the allowed set."
    category: ClassVar[FilterCategory] = CATEGORY
    scored: ClassVar[bool] = False
    param_schema: ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec(
            name="tiers",
            label="Allowed tiers",
            kind="tier_set",
            default=TIER_ALLOWED_DEFAULT,
        ),
    )

    def evaluate(self, ctx: FilterContext, params: Mapping[str, Any]) -> FilterResult:
        allowed_raw = params.get("tiers", TIER_ALLOWED_DEFAULT)
        allowed = {int(t) for t in allowed_raw}
        tier = ctx.ticker.tier
        if tier is None:
            return ineligible("missing_tier")
        passed = tier in allowed
        return FilterResult(passed=passed, value=tier)
