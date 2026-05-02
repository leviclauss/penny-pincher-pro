"""Tier 2 filter unit tests."""

from __future__ import annotations

from screener.filters import volatility
from tests.fixtures.contexts import make_context


def test_iv_rank_high_passes() -> None:
    ctx = make_context(indicators={"iv_rank": 75.0})
    r = volatility.IvRankHigh().evaluate(ctx, {"min": 50})
    assert r.passed is True
    assert r.value == 75.0
    assert r.score == 0.75


def test_iv_rank_high_fails_below_min() -> None:
    ctx = make_context(indicators={"iv_rank": 30.0})
    assert volatility.IvRankHigh().evaluate(ctx, {"min": 50}).passed is False


def test_iv_rank_high_warmup_ineligible() -> None:
    ctx = make_context(indicators={"iv_rank": None})
    r = volatility.IvRankHigh().evaluate(ctx, {})
    assert r.eligible is False
    assert r.reason == "iv_rank_warmup"


def test_iv_percentile_high_passes() -> None:
    ctx = make_context(indicators={"iv_percentile": 60.0})
    r = volatility.IvPercentileHigh().evaluate(ctx, {"min": 50})
    assert r.passed is True
    assert r.score == 0.6


def test_iv_percentile_high_warmup_ineligible() -> None:
    ctx = make_context(indicators={"iv_percentile": None})
    assert volatility.IvPercentileHigh().evaluate(ctx, {}).eligible is False


def test_iv_above_hv_passes_for_premium_rich() -> None:
    ctx = make_context(indicators={"iv_atm": 0.36, "hv_20": 0.24})
    r = volatility.IvAboveHv().evaluate(ctx, {"min_ratio": 1.2})
    assert r.passed is True
    assert r.value == 1.5


def test_iv_above_hv_fails_when_iv_low() -> None:
    ctx = make_context(indicators={"iv_atm": 0.20, "hv_20": 0.24})
    assert volatility.IvAboveHv().evaluate(ctx, {"min_ratio": 1.2}).passed is False


def test_iv_above_hv_ineligible_without_iv() -> None:
    ctx = make_context(indicators={"iv_atm": None, "hv_20": 0.20})
    r = volatility.IvAboveHv().evaluate(ctx, {})
    assert r.eligible is False
    assert r.reason == "missing_iv_atm"


def test_iv_above_hv_ineligible_with_zero_hv() -> None:
    ctx = make_context(indicators={"iv_atm": 0.30, "hv_20": 0.0})
    r = volatility.IvAboveHv().evaluate(ctx, {})
    assert r.eligible is False
