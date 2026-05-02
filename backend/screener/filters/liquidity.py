"""Tier 3 — Options liquidity filters.

``option_spread_pct`` works on the Alpaca free tier (uses bid/ask).

``option_oi_min`` and ``option_volume_min`` rely on ``open_interest`` and
``volume`` respectively, both of which the Alpaca free-tier snapshot
endpoint does not populate (see CLAUDE.md schema notes). These filters
detect missing data and return ``ineligible(...)`` so configs can
reference them without crashing; the implementations themselves are
correct and will start working as soon as a paid feed (ORATS/CBOE) lands
those columns.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ingestion.options_client import OptionSnapshotRecord
from screener.filters.base import FilterContext, FilterResult, ineligible

OPTION_SPREAD_PCT_DEFAULT_MAX = 0.10
OPTION_OI_MIN_DEFAULT = 500
OPTION_VOLUME_MIN_DEFAULT = 100


def _put_chain(ctx: FilterContext) -> list[OptionSnapshotRecord] | None:
    if ctx.options_chain is None:
        return None
    return [c for c in ctx.options_chain if c.option_type == "put"]


class OptionSpreadPct:
    """Median ``(ask-bid)/mid`` across the put chain ≤ ``max``.

    Until target-strike selection lands in PR4, the chain median acts as
    a chain-quality proxy. Once a strike is chosen, this filter can be
    re-scored against the selected contract specifically.
    """

    id: ClassVar[str] = "option_spread_pct"

    def evaluate(self, ctx: FilterContext, params: Mapping[str, Any]) -> FilterResult:
        max_spread = float(params.get("max", OPTION_SPREAD_PCT_DEFAULT_MAX))
        chain = _put_chain(ctx)
        if chain is None:
            return ineligible("no_options_chain")
        spreads: list[float] = []
        for c in chain:
            if c.bid is None or c.ask is None:
                continue
            mid = (c.bid + c.ask) / 2
            if mid <= 0:
                continue
            spreads.append((c.ask - c.bid) / mid)
        if not spreads:
            return ineligible("no_quoted_puts_in_chain")
        spreads.sort()
        median = spreads[len(spreads) // 2]
        passed = median <= max_spread
        return FilterResult(passed=passed, value=median)


class OptionOpenInterestMin:
    """Max put-chain open interest ≥ ``min``.

    Always ineligible on the Alpaca free tier (``open_interest`` is None
    for every row). The check itself is real — it'll start passing as
    soon as a paid feed populates the column.
    """

    id: ClassVar[str] = "option_oi_min"

    def evaluate(self, ctx: FilterContext, params: Mapping[str, Any]) -> FilterResult:
        min_oi = int(params.get("min", OPTION_OI_MIN_DEFAULT))
        chain = _put_chain(ctx)
        if chain is None:
            return ineligible("no_options_chain")
        values = [c.open_interest for c in chain if c.open_interest is not None]
        if not values:
            return ineligible("not_available_on_free_tier")
        max_oi = max(values)
        passed = max_oi >= min_oi
        return FilterResult(passed=passed, value=float(max_oi))


class OptionVolumeMin:
    """Max put-chain daily volume ≥ ``min``.

    Always ineligible on the Alpaca free tier (``volume`` is None for
    every row); see ``OptionOpenInterestMin`` for the same caveat.
    """

    id: ClassVar[str] = "option_volume_min"

    def evaluate(self, ctx: FilterContext, params: Mapping[str, Any]) -> FilterResult:
        min_vol = int(params.get("min", OPTION_VOLUME_MIN_DEFAULT))
        chain = _put_chain(ctx)
        if chain is None:
            return ineligible("no_options_chain")
        values = [c.volume for c in chain if c.volume is not None]
        if not values:
            return ineligible("not_available_on_free_tier")
        max_vol = max(values)
        passed = max_vol >= min_vol
        return FilterResult(passed=passed, value=float(max_vol))
