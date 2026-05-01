"""Tests for IV computation.

ATM IV uses a hand-constructed chain where the answer is obvious. BS
inversion is verified by pricing a known-vol option, stripping the IV from
the snapshot, and confirming we recover sigma to ~4 decimals. Rank and
percentile are tested for warm-up rules and known histories.
"""

from __future__ import annotations

from datetime import date

import pytest
from py_vollib.black_scholes import black_scholes

from ingestion.iv import (
    compute_atm_iv,
    compute_iv_percentile,
    compute_iv_rank,
)
from ingestion.options_client import OptionSnapshotRecord


def _record(
    *,
    strike: float,
    option_type: str,
    expiration: date,
    iv: float | None = None,
    bid: float | None = None,
    ask: float | None = None,
) -> OptionSnapshotRecord:
    return OptionSnapshotRecord(
        symbol="AAPL",
        expiration=expiration,
        strike=strike,
        option_type=option_type,
        bid=bid,
        ask=ask,
        last=None,
        volume=None,
        open_interest=None,
        delta=None,
        gamma=None,
        theta=None,
        vega=None,
        iv=iv,
    )


def test_atm_iv_averages_call_and_put_at_nearest_strike() -> None:
    today = date(2024, 5, 1)
    expiration = date(2024, 5, 17)
    chain = [
        _record(strike=170.0, option_type="call", expiration=expiration, iv=0.28),
        _record(strike=170.0, option_type="put", expiration=expiration, iv=0.30),
        _record(strike=175.0, option_type="call", expiration=expiration, iv=0.40),
    ]
    atm = compute_atm_iv(chain, spot=171.0, as_of=today)
    assert atm == pytest.approx(0.29, abs=1e-9)


def test_atm_iv_picks_front_month_skipping_too_close() -> None:
    today = date(2024, 5, 1)
    too_close = date(2024, 5, 5)
    next_expiry = date(2024, 5, 17)
    chain = [
        _record(strike=170.0, option_type="call", expiration=too_close, iv=0.50),
        _record(strike=170.0, option_type="call", expiration=next_expiry, iv=0.28),
        _record(strike=170.0, option_type="put", expiration=next_expiry, iv=0.30),
    ]
    atm = compute_atm_iv(chain, spot=170.0, as_of=today, min_dte=7)
    assert atm == pytest.approx(0.29, abs=1e-9)


def test_atm_iv_returns_none_when_only_short_dte_available() -> None:
    today = date(2024, 5, 1)
    chain = [_record(strike=170.0, option_type="call", expiration=date(2024, 5, 5), iv=0.5)]
    assert compute_atm_iv(chain, spot=170.0, as_of=today, min_dte=7) is None


def test_atm_iv_falls_back_to_black_scholes_when_iv_missing() -> None:
    today = date(2024, 5, 1)
    expiration = date(2024, 5, 31)
    dte_years = (expiration - today).days / 365.0
    spot = 100.0
    strike = 100.0
    sigma_call = 0.30
    sigma_put = 0.28

    call_price = black_scholes(flag="c", S=spot, K=strike, t=dte_years, r=0.045, sigma=sigma_call)
    put_price = black_scholes(flag="p", S=spot, K=strike, t=dte_years, r=0.045, sigma=sigma_put)

    chain = [
        _record(
            strike=strike,
            option_type="call",
            expiration=expiration,
            bid=call_price - 0.01,
            ask=call_price + 0.01,
        ),
        _record(
            strike=strike,
            option_type="put",
            expiration=expiration,
            bid=put_price - 0.01,
            ask=put_price + 0.01,
        ),
    ]
    atm = compute_atm_iv(chain, spot=spot, as_of=today, risk_free_rate=0.045)
    expected_avg = (sigma_call + sigma_put) / 2
    assert atm is not None
    assert atm == pytest.approx(expected_avg, abs=1e-3)


def test_atm_iv_returns_none_when_chain_unusable() -> None:
    today = date(2024, 5, 1)
    expiration = date(2024, 5, 17)
    chain = [
        _record(strike=170.0, option_type="call", expiration=expiration, bid=None, ask=None),
    ]
    assert compute_atm_iv(chain, spot=170.0, as_of=today) is None


def test_iv_rank_warm_up_then_value() -> None:
    history_short = [0.2] * 50
    assert compute_iv_rank(history_short, 0.25) is None

    history = [0.10 + 0.001 * i for i in range(252)]
    rank_high = compute_iv_rank(history, 0.351)
    assert rank_high == pytest.approx(1.0, abs=1e-6)
    rank_low = compute_iv_rank(history, 0.10)
    assert rank_low == pytest.approx(0.0, abs=1e-6)
    rank_mid = compute_iv_rank(history, 0.2255)
    assert rank_mid == pytest.approx(0.5, abs=1e-3)


def test_iv_rank_clamps_to_unit_range() -> None:
    history = [0.20] * 200
    assert compute_iv_rank(history, 0.50) == 0.5
    history2 = [0.10 + 0.001 * i for i in range(200)]
    assert compute_iv_rank(history2, 0.05) == 0.0
    assert compute_iv_rank(history2, 1.0) == 1.0


def test_iv_percentile() -> None:
    history = [0.10 + 0.001 * i for i in range(252)]

    assert compute_iv_percentile(history[:50], 0.20) is None

    pct_low = compute_iv_percentile(history, 0.10)
    assert pct_low == 0.0
    pct_high = compute_iv_percentile(history, 0.99)
    assert pct_high == 1.0
    pct_mid = compute_iv_percentile(history, 0.235)
    assert pct_mid is not None
    assert 0.4 < pct_mid < 0.6


def test_iv_history_filters_zero_and_none() -> None:
    history: list[float | None] = [0.0, None, 0.10, 0.15, 0.20] * 30
    rank = compute_iv_rank(history, 0.18, min_history=80)
    assert rank is not None
    assert 0.0 < rank < 1.0
