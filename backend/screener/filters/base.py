"""Filter contract: the inputs, the outcome, and the Protocol every filter satisfies.

A filter is a tiny stateless class with a class-level ``id`` string and a single
``evaluate(ctx, params) -> FilterResult`` method. The pipeline iterates filters
in config order, short-circuiting on a hard fail of a *required* filter. See
``docs/planning/02-screener-filters.md`` for the catalog and config JSON shape.

Point-in-time correctness is the caller's responsibility: every field on
``FilterContext`` must contain only data observable at ``as_of``. The pipeline's
context builder enforces this by date-filtering its DB queries; filters trust
what they're handed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, ClassVar, Literal, Protocol, TypeAlias

import pandas as pd

from db.models.market import Ticker
from ingestion.options_client import OptionSnapshotRecord

FilterCategory: TypeAlias = Literal["trend", "volatility", "liquidity", "event"]
ParamKind: TypeAlias = Literal["number", "integer", "percent", "currency", "tier_set"]


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """Machine-readable schema for one configurable filter parameter.

    Drives the config-editor UI: ``kind`` picks the input control
    (``percent`` is a fractional float displayed as %; ``currency`` is
    USD; ``tier_set`` is a multi-select over allowed ticker tiers).
    ``default`` mirrors the constant the filter actually consumes so UI
    defaults can never drift from runtime defaults.
    """

    name: str
    label: str
    kind: ParamKind
    default: float | int | tuple[int, ...]
    min: float | None = None
    max: float | None = None
    step: float | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class FilterContext:
    """Read-only inputs for a single (symbol, as_of) evaluation.

    ``bars`` is indexed by date ascending and must be point-in-time
    (rows with date > ``as_of`` excluded by the caller). ``indicators``
    is the ``indicators_daily`` row at ``as_of`` (or ``None`` if missing).
    ``options_chain`` is the *current* snapshot only — the table holds no
    history, so for backtests this will be ``None`` whenever ``as_of``
    isn't today and any options-dependent filter must mark itself
    ineligible.
    """

    symbol: str
    as_of: date
    bars: pd.DataFrame
    indicators: pd.Series | None
    options_chain: list[OptionSnapshotRecord] | None
    earnings: list[date]
    ticker: Ticker
    macro: pd.Series | None

    def latest_close(self) -> float | None:
        if self.bars.empty:
            return None
        return float(self.bars["close"].iloc[-1])


@dataclass(frozen=True, slots=True)
class FilterResult:
    """Outcome of evaluating one filter against one ticker.

    ``eligible=False`` means the filter could not be evaluated (e.g. NULL
    inputs during indicator warmup, or no options chain for a backtest
    date). The pipeline treats an ineligible *required* filter as a hard
    fail for the symbol; an ineligible *optional* filter contributes no
    weight to scoring rather than dragging the score down.
    """

    passed: bool
    eligible: bool = True
    score: float | None = None
    value: float | str | dict[str, Any] | None = None
    reason: str | None = None


class Filter(Protocol):
    """The shape every filter implements.

    Implementations are stateless classes with a class-level ``id`` matching
    the string used in ``filter_configs.config_json`` and ``FILTER_REGISTRY``.
    The remaining class vars (``label``, ``description``, ``category``,
    ``param_schema``, ``scored``) feed the catalog endpoint at
    ``GET /api/screener/filters`` and the config-editor UI; see
    ``docs/planning/11-screener-config-ui.md``.
    """

    id: ClassVar[str]
    label: ClassVar[str]
    description: ClassVar[str]
    category: ClassVar[FilterCategory]
    param_schema: ClassVar[tuple[ParamSpec, ...]]
    scored: ClassVar[bool]

    def evaluate(self, ctx: FilterContext, params: Mapping[str, Any]) -> FilterResult: ...


def ineligible(reason: str, value: float | str | dict[str, Any] | None = None) -> FilterResult:
    """Shorthand for the 'cannot evaluate' branch.

    Returns ``passed=False, eligible=False`` so a required filter
    short-circuits the symbol while an optional one drops out of scoring
    cleanly. ``reason`` shows up in ``filter_results_json`` for the UI's
    "why didn't this fire?" debug view, so make it specific.
    """
    return FilterResult(passed=False, eligible=False, value=value, reason=reason)
