"""Tier 5 — Wheel economics filter.

Selects the best put strike for a cash-secured put within a configurable
DTE window and scores by annualized return on capital. The filter requires
a live options chain (``ctx.options_chain is not None``), so it is always
ineligible in backtests where the chain is ``None`` for historical dates.

Annualized return = (premium / strike) * (365 / dte)

where ``premium = (bid + ask) / 2`` and ``strike`` is the put closest in
absolute-delta to ``delta_target``. Put deltas are negative from the API;
``abs(delta)`` is used throughout so the caller can specify e.g. ``0.30``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date as DateType
from typing import Any, ClassVar

from ingestion.options_client import OptionSnapshotRecord
from screener.filters.base import (
    FilterCategory,
    FilterContext,
    FilterResult,
    ParamSpec,
    ineligible,
)

CATEGORY: FilterCategory = "volatility"

DEFAULT_MIN_DTE = 30
DEFAULT_MAX_DTE = 45
DEFAULT_DELTA_TARGET = 0.30
DEFAULT_MIN_ANN_RETURN = 0.10  # 10% annualized


class PremiumEconomics:
    """Select target CSP strike and score by annualized return.

    Finds the put in the ``[min_dte, max_dte]`` window whose absolute delta
    is closest to ``delta_target``, then computes the annualized return on
    the strike collateral. Passes when ``annualized_return >= min_annualized_return``.

    Returns a ``value`` dict consumed by the pipeline to populate
    ``screener_results.(target_strike, target_expiration, target_premium,
    target_delta, annualized_return)``.
    """

    id: ClassVar[str] = "premium_economics"
    label: ClassVar[str] = "Premium Economics (CSP)"
    description: ClassVar[str] = (
        "Selects the CSP strike nearest to delta_target within the DTE window "
        "and scores by annualized return. Ineligible without a live options chain."
    )
    category: ClassVar[FilterCategory] = CATEGORY
    scored: ClassVar[bool] = True
    param_schema: ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec(
            name="min_dte",
            label="Min DTE",
            kind="integer",
            default=DEFAULT_MIN_DTE,
            min=1,
            max=90,
            step=1,
            description="Earliest expiration (trading days from today) to consider.",
        ),
        ParamSpec(
            name="max_dte",
            label="Max DTE",
            kind="integer",
            default=DEFAULT_MAX_DTE,
            min=1,
            max=180,
            step=1,
            description="Latest expiration (trading days from today) to consider.",
        ),
        ParamSpec(
            name="delta_target",
            label="Target Delta",
            kind="number",
            default=DEFAULT_DELTA_TARGET,
            min=0.05,
            max=0.70,
            step=0.01,
            description="Absolute delta target for the put (e.g. 0.30 = 30-delta).",
        ),
        ParamSpec(
            name="min_annualized_return",
            label="Min Ann. Return",
            kind="percent",
            default=DEFAULT_MIN_ANN_RETURN,
            min=0.0,
            max=2.0,
            step=0.01,
            description="Minimum annualized return (as a fraction) to pass.",
        ),
    )

    def evaluate(self, ctx: FilterContext, params: Mapping[str, Any]) -> FilterResult:
        if not ctx.options_chain:
            return ineligible("no_options_chain")

        min_dte = int(params.get("min_dte", DEFAULT_MIN_DTE))
        max_dte = int(params.get("max_dte", DEFAULT_MAX_DTE))
        delta_target = float(params.get("delta_target", DEFAULT_DELTA_TARGET))
        min_ann_return = float(params.get("min_annualized_return", DEFAULT_MIN_ANN_RETURN))

        candidate = _select_strike(ctx.options_chain, ctx.as_of, min_dte, max_dte, delta_target)
        if candidate is None:
            return ineligible("no_qualifying_strike")

        opt, dte = candidate
        if opt.bid is None or opt.ask is None or opt.bid <= 0 or opt.ask <= 0:
            return ineligible("no_valid_bid_ask")

        premium = (opt.bid + opt.ask) / 2.0
        ann_return = (premium / opt.strike) * (365.0 / dte)
        passed = ann_return >= min_ann_return
        # Score scales 0..1 over [0, 2*min_ann_return]; higher return = higher score.
        score = min(ann_return / (min_ann_return * 2.0), 1.0) if min_ann_return > 0 else 0.0

        value: dict[str, Any] = {
            "strike": opt.strike,
            "expiration": opt.expiration.isoformat(),
            "dte": dte,
            "premium": round(premium, 4),
            "delta": round(abs(opt.delta), 4) if opt.delta is not None else None,
            "annualized_return": round(ann_return, 4),
        }
        return FilterResult(passed=passed, score=score, value=value)


def _select_strike(
    chain: list[OptionSnapshotRecord],
    as_of: DateType,
    min_dte: int,
    max_dte: int,
    delta_target: float,
) -> tuple[OptionSnapshotRecord, int] | None:
    best: tuple[OptionSnapshotRecord, int] | None = None
    best_delta_diff = float("inf")

    for opt in chain:
        if opt.option_type != "put":
            continue
        if opt.delta is None:
            continue
        dte = (opt.expiration - as_of).days
        if dte < min_dte or dte > max_dte:
            continue
        diff = abs(abs(opt.delta) - delta_target)
        if diff < best_delta_diff:
            best_delta_diff = diff
            best = (opt, dte)

    return best
