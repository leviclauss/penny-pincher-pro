"""Tier 3 filter unit tests.

Validates that ``option_oi_min`` / ``option_volume_min`` correctly land in
the ineligible bucket when the snapshot lacks open-interest / volume data
(the Alpaca free-tier reality), and that they would actually compare
against the threshold if a paid feed populated those columns.
"""

from __future__ import annotations

from screener.filters import liquidity
from tests.fixtures.contexts import make_context, make_put


def test_option_spread_pct_passes_for_tight_chain() -> None:
    chain = [
        make_put(strike=95, bid=1.00, ask=1.04),  # spread 0.04 / mid 1.02 ≈ 0.039
        make_put(strike=100, bid=1.50, ask=1.55),  # ≈ 0.0328
    ]
    ctx = make_context(options_chain=chain)
    r = liquidity.OptionSpreadPct().evaluate(ctx, {"max": 0.10})
    assert r.passed is True


def test_option_spread_pct_fails_for_wide_chain() -> None:
    chain = [
        make_put(strike=95, bid=1.00, ask=1.30),  # ≈ 0.26
        make_put(strike=100, bid=1.50, ask=1.95),  # ≈ 0.26
    ]
    ctx = make_context(options_chain=chain)
    r = liquidity.OptionSpreadPct().evaluate(ctx, {"max": 0.10})
    assert r.passed is False


def test_option_spread_pct_ineligible_without_chain() -> None:
    ctx = make_context(options_chain=None)
    r = liquidity.OptionSpreadPct().evaluate(ctx, {})
    assert r.eligible is False
    assert r.reason == "no_options_chain"


def test_option_spread_pct_ineligible_when_no_quotes() -> None:
    chain = [make_put(strike=100, bid=None, ask=None)]
    ctx = make_context(options_chain=chain)
    r = liquidity.OptionSpreadPct().evaluate(ctx, {})
    assert r.eligible is False
    assert r.reason == "no_quoted_puts_in_chain"


def test_option_spread_pct_ignores_calls() -> None:
    # OptionSnapshotRecord has option_type='put' from make_put; build a
    # mixed chain by manually flipping a record's type.
    from dataclasses import replace

    puts = [make_put(strike=95, bid=1.00, ask=1.04)]
    calls = [replace(make_put(strike=95, bid=10.0, ask=20.0), option_type="call")]
    ctx = make_context(options_chain=puts + calls)
    r = liquidity.OptionSpreadPct().evaluate(ctx, {"max": 0.10})
    assert r.passed is True  # call's wide spread is ignored


def test_option_oi_min_ineligible_when_oi_is_null() -> None:
    chain = [make_put(strike=100, open_interest=None)]
    ctx = make_context(options_chain=chain)
    r = liquidity.OptionOpenInterestMin().evaluate(ctx, {"min": 500})
    assert r.eligible is False
    assert r.reason == "not_available_on_free_tier"


def test_option_oi_min_pass_when_data_present() -> None:
    chain = [
        make_put(strike=100, open_interest=600),
        make_put(strike=95, open_interest=200),
    ]
    ctx = make_context(options_chain=chain)
    r = liquidity.OptionOpenInterestMin().evaluate(ctx, {"min": 500})
    assert r.passed is True
    assert r.value == 600.0


def test_option_oi_min_fail_when_below_threshold() -> None:
    chain = [make_put(strike=100, open_interest=200)]
    ctx = make_context(options_chain=chain)
    r = liquidity.OptionOpenInterestMin().evaluate(ctx, {"min": 500})
    assert r.passed is False


def test_option_volume_min_ineligible_when_volume_is_null() -> None:
    chain = [make_put(strike=100, volume=None)]
    ctx = make_context(options_chain=chain)
    r = liquidity.OptionVolumeMin().evaluate(ctx, {"min": 100})
    assert r.eligible is False


def test_option_volume_min_pass_when_data_present() -> None:
    chain = [make_put(strike=100, volume=250)]
    ctx = make_context(options_chain=chain)
    r = liquidity.OptionVolumeMin().evaluate(ctx, {"min": 100})
    assert r.passed is True
    assert r.value == 250.0
