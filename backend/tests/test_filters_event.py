"""Tier 4 single-ticker filter unit tests."""

from __future__ import annotations

from datetime import date, timedelta

from screener.filters import event
from tests.fixtures.contexts import make_context, make_ticker

AS_OF = date(2024, 6, 3)


def test_no_earnings_pass_when_window_clear() -> None:
    ctx = make_context(as_of=AS_OF, earnings=[AS_OF + timedelta(days=60)])
    r = event.NoEarningsInWindow().evaluate(ctx, {"days": 45})
    assert r.passed is True
    assert r.value is None


def test_no_earnings_fail_when_inside_window() -> None:
    ctx = make_context(as_of=AS_OF, earnings=[AS_OF + timedelta(days=20)])
    r = event.NoEarningsInWindow().evaluate(ctx, {"days": 45})
    assert r.passed is False
    assert r.value == (AS_OF + timedelta(days=20)).isoformat()


def test_no_earnings_default_window() -> None:
    # Default = 45 days; earnings 50 days out should pass.
    ctx = make_context(as_of=AS_OF, earnings=[AS_OF + timedelta(days=50)])
    assert event.NoEarningsInWindow().evaluate(ctx, {}).passed is True


def test_no_earnings_picks_soonest_when_multiple() -> None:
    ctx = make_context(
        as_of=AS_OF,
        earnings=[AS_OF + timedelta(days=10), AS_OF + timedelta(days=30)],
    )
    r = event.NoEarningsInWindow().evaluate(ctx, {"days": 45})
    assert r.value == (AS_OF + timedelta(days=10)).isoformat()


def test_min_market_cap_pass() -> None:
    ctx = make_context(ticker=make_ticker(market_cap=20_000_000_000.0))
    r = event.MinMarketCap().evaluate(ctx, {"min_usd": 10_000_000_000})
    assert r.passed is True


def test_min_market_cap_fail() -> None:
    ctx = make_context(ticker=make_ticker(market_cap=2_000_000_000.0))
    r = event.MinMarketCap().evaluate(ctx, {"min_usd": 10_000_000_000})
    assert r.passed is False


def test_min_market_cap_ineligible_without_data() -> None:
    ctx = make_context(ticker=make_ticker(market_cap=None))
    assert event.MinMarketCap().evaluate(ctx, {}).eligible is False


def test_tier_allowed_pass() -> None:
    ctx = make_context(ticker=make_ticker(tier=1))
    r = event.TierAllowed().evaluate(ctx, {"tiers": [1, 2]})
    assert r.passed is True


def test_tier_allowed_fail() -> None:
    ctx = make_context(ticker=make_ticker(tier=3))
    r = event.TierAllowed().evaluate(ctx, {"tiers": [1, 2]})
    assert r.passed is False


def test_tier_allowed_ineligible_without_tier() -> None:
    ctx = make_context(ticker=make_ticker(tier=None))
    assert event.TierAllowed().evaluate(ctx, {}).eligible is False


def test_sector_allowed_empty_list_passes_everything() -> None:
    ctx = make_context(ticker=make_ticker(sector="Technology"))
    r = event.SectorAllowed().evaluate(ctx, {"sectors": []})
    assert r.passed is True
    assert r.eligible is True


def test_sector_allowed_pass_when_in_set() -> None:
    ctx = make_context(ticker=make_ticker(sector="Technology"))
    r = event.SectorAllowed().evaluate(ctx, {"sectors": ["Technology", "Health Care"]})
    assert r.passed is True
    assert r.value == "Technology"


def test_sector_allowed_fail_when_not_in_set() -> None:
    ctx = make_context(ticker=make_ticker(sector="Energy"))
    r = event.SectorAllowed().evaluate(ctx, {"sectors": ["Technology", "Health Care"]})
    assert r.passed is False


def test_sector_allowed_ineligible_when_sector_missing() -> None:
    ctx = make_context(ticker=make_ticker(sector=None))
    r = event.SectorAllowed().evaluate(ctx, {"sectors": ["Technology"]})
    assert r.eligible is False
    assert r.reason == "missing_sector"
