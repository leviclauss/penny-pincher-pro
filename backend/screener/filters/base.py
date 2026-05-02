"""Filter contract: every screener filter is a pure function of a context.

Filters take a ``FilterContext`` (the day's data for one ticker, frozen at
``as_of``) plus a params dict and return a ``FilterResult`` describing
pass/fail, an optional 0-1 score for weighted ranking, the value being
thresholded (so the UI can render "RSI 28 < 35"), and a human reason on
failure or skip.

Filters are ``Protocol``-typed rather than ABC-subclassed so they can be
declared as plain dataclasses or singletons; the registry stores classes
and instantiates them with no args.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, ClassVar, Protocol, runtime_checkable

import pandas as pd

from db.models.market import Ticker


@dataclass(frozen=True)
class FilterContext:
    """Point-in-time snapshot of one ticker's data on ``as_of``.

    ``bars`` includes every row with index date <= ``as_of`` (oldest first),
    so filters that need lookback windows can slice without worrying about
    leakage. ``indicators`` is the row from ``indicators_daily`` for
    ``(symbol, as_of)``; missing columns surface as NaN. ``options_chain``
    is the current snapshot — note it is NOT point-in-time historical and
    must not be used by backtests.
    """

    symbol: str
    as_of: date
    bars: pd.DataFrame
    indicators: pd.Series
    options_chain: pd.DataFrame | None
    earnings: list[date]
    ticker: Ticker


@dataclass(frozen=True)
class FilterResult:
    passed: bool
    score: float | None = None
    value: float | str | None = None
    reason: str | None = None


@runtime_checkable
class Filter(Protocol):
    """Filter contract.

    Implementations declare a class-level ``id`` and an ``evaluate`` method.
    The registry instantiates them with no arguments — keep ``__init__``
    parameter-free; tunables belong in ``params``.
    """

    id: ClassVar[str]

    def evaluate(self, ctx: FilterContext, params: dict[str, Any]) -> FilterResult: ...


__all__ = ["Filter", "FilterContext", "FilterResult"]
