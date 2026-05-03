"""Intraday trigger payload builders.

Pure functions that read context (no DB, no SDK) and return the dict the
matching Telegram template expects. The intraday scheduler job
(``scheduler/jobs/intraday.py``) handles polling, freshness, dedup, and
dispatch — these builders just shape the payload.

Two families:

- ``setup_triggered`` — a watchlist symbol started passing a screener config
  during RTH that *wasn't* in this morning's digest. One alert per symbol per
  day.
- ``iv_spike`` — intraday ATM IV jumped ≥ a configurable percent vs the
  most recent stored ``indicators_daily.iv_atm`` (yesterday's close IV in
  practice). One alert per symbol per day.

Both payloads include ``as_of`` and ``symbol`` so the shared
``already_dispatched_for_symbol_on`` dedup helper finds them.
"""

from __future__ import annotations

from datetime import date
from typing import Any

SETUP_TRIGGERED = "setup_triggered"
IV_SPIKE = "iv_spike"


def build_setup_payload(
    *,
    symbol: str,
    as_of: date,
    config_name: str,
    config_id: int | None,
    close: float,
    score: float | None,
    rsi: float | None,
    iv_percentile: float | None,
) -> dict[str, Any]:
    """Build the ``setup_triggered`` payload for one (symbol, config) hit.

    ``iv_percentile`` is the DB-side fraction in [0, 1]; we scale it to
    [0, 100] for display so a "67" reads as 67th percentile rather than
    0.67.
    """
    ivp_pct = iv_percentile * 100 if iv_percentile is not None else None
    return {
        "as_of": as_of.isoformat(),
        "symbol": symbol,
        "config": config_name,
        "config_id": config_id,
        "close": float(close),
        "score": float(score) if score is not None else 0.0,
        "rsi": _fmt_number(rsi, digits=0),
        "ivp": _fmt_number(ivp_pct, digits=0),
    }


def build_iv_spike_payload(
    *,
    symbol: str,
    as_of: date,
    baseline_iv: float,
    current_iv: float,
    close: float,
) -> dict[str, Any]:
    """Build the ``iv_spike`` payload — current vs baseline ATM IV."""
    pct_change = (current_iv - baseline_iv) / baseline_iv if baseline_iv > 0 else 0.0
    return {
        "as_of": as_of.isoformat(),
        "symbol": symbol,
        "baseline_iv": float(baseline_iv),
        "current_iv": float(current_iv),
        "pct_change": float(pct_change),
        "close": float(close),
    }


def _fmt_number(value: float | None, *, digits: int) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"
