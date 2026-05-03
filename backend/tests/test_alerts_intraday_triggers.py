"""Pure-function tests for the intraday payload builders."""

from __future__ import annotations

from datetime import date

import pytest

from alerts.triggers.intraday import build_iv_spike_payload, build_setup_payload


def test_setup_payload_shape_and_dedup_keys() -> None:
    payload = build_setup_payload(
        symbol="AAPL",
        as_of=date(2026, 5, 4),
        config_name="Conservative Wheel",
        config_id=7,
        close=172.40,
        score=0.81,
        rsi=32.0,
        iv_percentile=0.67,
    )

    # Required dedup keys for `already_dispatched_for_symbol_on`.
    assert payload["as_of"] == "2026-05-04"
    assert payload["symbol"] == "AAPL"

    # Surfaced fields drive the Telegram template.
    assert payload["config"] == "Conservative Wheel"
    assert payload["config_id"] == 7
    assert payload["close"] == 172.40
    assert payload["score"] == 0.81
    assert payload["rsi"] == "32"
    # 0.67 → 67% displayed.
    assert payload["ivp"] == "67"


def test_setup_payload_handles_missing_optional_indicators() -> None:
    payload = build_setup_payload(
        symbol="MSFT",
        as_of=date(2026, 5, 4),
        config_name="Whatever",
        config_id=None,
        close=300.0,
        score=None,
        rsi=None,
        iv_percentile=None,
    )

    assert payload["score"] == 0.0
    assert payload["rsi"] == "—"
    assert payload["ivp"] == "—"
    assert payload["config_id"] is None


def test_iv_spike_payload_computes_pct_change() -> None:
    payload = build_iv_spike_payload(
        symbol="AAPL",
        as_of=date(2026, 5, 4),
        baseline_iv=0.20,
        current_iv=0.30,
        close=170.0,
    )

    assert payload["as_of"] == "2026-05-04"
    assert payload["symbol"] == "AAPL"
    assert payload["baseline_iv"] == 0.20
    assert payload["current_iv"] == 0.30
    assert payload["pct_change"] == pytest.approx(0.5)
    assert payload["close"] == 170.0


def test_iv_spike_payload_zero_baseline_returns_zero_pct() -> None:
    payload = build_iv_spike_payload(
        symbol="AAPL",
        as_of=date(2026, 5, 4),
        baseline_iv=0.0,
        current_iv=0.30,
        close=170.0,
    )

    # Builder is defensive — the job already filters baseline > 0, but a
    # zero slipping through must not crash on division.
    assert payload["pct_change"] == 0.0
