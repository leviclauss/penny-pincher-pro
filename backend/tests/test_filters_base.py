"""Tests for the filter contract: ``ineligible`` helper, defaults, ``latest_close``."""

from __future__ import annotations

import pandas as pd

from screener.filters.base import FilterResult, ineligible
from tests.fixtures.contexts import constant_bars, make_context


def test_ineligible_marks_failed_and_not_eligible() -> None:
    r = ineligible("missing_thing", value=1.5)
    assert r.passed is False
    assert r.eligible is False
    assert r.score is None
    assert r.value == 1.5
    assert r.reason == "missing_thing"


def test_filter_result_defaults_to_eligible_no_score() -> None:
    r = FilterResult(passed=True, value=42.0)
    assert r.eligible is True
    assert r.score is None
    assert r.reason is None


def test_latest_close_returns_none_for_empty_bars() -> None:
    ctx = make_context()
    assert ctx.latest_close() is None


def test_latest_close_returns_last_bar_close() -> None:
    bars = pd.DataFrame(
        {
            "open": [1.0, 2.0],
            "high": [1.0, 2.0],
            "low": [1.0, 2.0],
            "close": [10.0, 20.0],
            "volume": [1, 2],
        },
        index=pd.DatetimeIndex(pd.bdate_range("2024-01-01", periods=2)),
    )
    ctx = make_context(bars=bars)
    assert ctx.latest_close() == 20.0


def test_latest_close_uses_constant_bars_helper() -> None:
    ctx = make_context(bars=constant_bars(123.45))
    assert ctx.latest_close() == 123.45
