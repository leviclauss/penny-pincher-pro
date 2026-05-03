"""Black-Scholes wrapper sanity checks.

These exist as a guardrail against signing or scaling bugs in the wrapper —
the underlying ``py_vollib`` math is well-tested upstream, so we only verify
wiring (right flag, right time-in-years, right delta sign).
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from backtest.pricing import (
    estimate_sigma,
    price_option,
    realized_vol_from_closes,
    select_call_strike,
    select_put_strike,
)


def test_atm_put_and_call_have_similar_value() -> None:
    today = date(2025, 1, 2)
    expiration = today + timedelta(days=30)
    put = price_option(
        option_type="p",
        spot=100.0,
        strike=100.0,
        as_of=today,
        expiration=expiration,
        sigma=0.30,
    )
    call = price_option(
        option_type="c",
        spot=100.0,
        strike=100.0,
        as_of=today,
        expiration=expiration,
        sigma=0.30,
    )
    # With small positive r, the call sits slightly above the put (BS parity).
    assert call.mid > put.mid
    assert (call.mid - put.mid) < 1.0
    assert put.delta < 0
    assert call.delta > 0
    # ATM put delta is around -0.5; ATM call around +0.5.
    assert -0.6 < put.delta < -0.4
    assert 0.4 < call.delta < 0.6


def test_deep_otm_put_is_cheap_and_low_delta() -> None:
    today = date(2025, 1, 2)
    quote = price_option(
        option_type="p",
        spot=100.0,
        strike=70.0,
        as_of=today,
        expiration=today + timedelta(days=30),
        sigma=0.30,
    )
    assert 0.0 <= quote.mid < 1.0
    assert -0.10 < quote.delta < 0.0


def test_select_put_strike_targets_30_delta() -> None:
    spot = 100.0
    strike = select_put_strike(
        spot=spot,
        target_delta=0.30,
        sigma=0.30,
        days_to_expiry=30,
    )
    # 30-delta put on a $100 / 30%-vol underlying lives in the 90s.
    assert spot * 0.85 < strike < spot * 0.99


def test_select_call_strike_floored_at_cost_basis() -> None:
    spot = 100.0
    cost_basis = 105.0
    strike = select_call_strike(
        spot=spot,
        cost_basis=cost_basis,
        target_delta=0.30,
        sigma=0.30,
        days_to_expiry=30,
    )
    assert strike >= cost_basis


def test_estimate_sigma_prefers_iv_atm() -> None:
    assert estimate_sigma(iv_atm=0.45, hv_20=0.30, realized_fallback=0.20) == 0.45


def test_estimate_sigma_falls_through_to_default() -> None:
    sigma = estimate_sigma(iv_atm=None, hv_20=None, realized_fallback=None)
    assert sigma > 0


def test_realized_vol_from_closes() -> None:
    closes = [100.0 * math.exp(0.01 * i) for i in range(30)]
    vol = realized_vol_from_closes(closes, window=20)
    assert vol is not None
    assert vol > 0


def test_realized_vol_handles_short_input() -> None:
    assert realized_vol_from_closes([1.0, 2.0, 3.0], window=20) is None


def test_invalid_flag_raises() -> None:
    with pytest.raises(ValueError):
        price_option(
            option_type="x",
            spot=100.0,
            strike=100.0,
            as_of=date(2025, 1, 2),
            expiration=date(2025, 2, 2),
            sigma=0.30,
        )
