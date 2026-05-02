"""String-ID → ``Filter`` class lookup.

An explicit dict (rather than decorator-based registration) so mypy --strict
can typecheck the mapping, import order is obvious, and the registry mirrors
``JOB_REGISTRY`` in ``scheduler/app.py``.

``sector_concentration`` from the catalog is intentionally absent: it's
cross-symbol and will land as a ``Postprocessor`` in PR3, not as a ``Filter``.
"""

from __future__ import annotations

from screener.filters import event, liquidity, technical, volatility
from screener.filters.base import Filter

FILTER_REGISTRY: dict[str, type[Filter]] = {
    # Tier 1 — Trend / Mean Reversion
    technical.Near200EMA.id: technical.Near200EMA,
    technical.Near50EMA.id: technical.Near50EMA,
    technical.WeeklyAbove200EMA.id: technical.WeeklyAbove200EMA,
    technical.RsiOversold.id: technical.RsiOversold,
    technical.BollingerLowerTouch.id: technical.BollingerLowerTouch,
    technical.NotFreefall.id: technical.NotFreefall,
    # Tier 2 — Volatility / Premium
    volatility.IvRankHigh.id: volatility.IvRankHigh,
    volatility.IvPercentileHigh.id: volatility.IvPercentileHigh,
    volatility.IvAboveHv.id: volatility.IvAboveHv,
    # Tier 3 — Options Liquidity
    liquidity.OptionSpreadPct.id: liquidity.OptionSpreadPct,
    liquidity.OptionOpenInterestMin.id: liquidity.OptionOpenInterestMin,
    liquidity.OptionVolumeMin.id: liquidity.OptionVolumeMin,
    # Tier 4 — Event / Risk (sector_concentration is a Postprocessor; see PR3)
    event.NoEarningsInWindow.id: event.NoEarningsInWindow,
    event.MinMarketCap.id: event.MinMarketCap,
    event.TierAllowed.id: event.TierAllowed,
}


class UnknownFilterError(KeyError):
    """Raised when a config references a filter ID not in ``FILTER_REGISTRY``."""


def resolve(filter_id: str) -> type[Filter]:
    try:
        return FILTER_REGISTRY[filter_id]
    except KeyError as err:
        raise UnknownFilterError(filter_id) from err


def all_ids() -> list[str]:
    return sorted(FILTER_REGISTRY.keys())
