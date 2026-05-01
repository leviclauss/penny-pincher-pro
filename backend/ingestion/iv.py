"""Implied-volatility computation.

Three pieces:

- ``compute_atm_iv(chain, spot)`` — average call/put IV at the front-month
  expiration's strike nearest spot. Falls back to Black-Scholes inversion of
  the option mid price when the snapshot didn't carry IV.
- ``compute_iv_rank(history, current)`` — ``(current - 52w_low) /
  (52w_high - 52w_low)``. Returns ``None`` when there's not enough history
  (default ≥126 days).
- ``compute_iv_percentile(history, current)`` — fraction of past-year days
  whose IV was below ``current``. Same minimum-history rule.

Risk-free rate for the BS inversion comes from settings (refresh quarterly).

History is a plain ``list[float]`` of recent ``iv_atm`` values, oldest first.
Caller is responsible for passing a 252-day window from ``indicators_daily``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from py_vollib.black_scholes.implied_volatility import implied_volatility

from core.config import get_settings
from core.logging import get_logger
from ingestion.options_client import OptionSnapshotRecord

log = get_logger(__name__)

DEFAULT_MIN_HISTORY = 126
DEFAULT_FULL_HISTORY = 252
MIN_DTE_FOR_ATM_IV = 7


def compute_atm_iv(
    chain: list[OptionSnapshotRecord],
    spot: float,
    *,
    as_of: date,
    risk_free_rate: float | None = None,
    min_dte: int = MIN_DTE_FOR_ATM_IV,
) -> float | None:
    """Average call/put IV at the front-month strike nearest spot.

    Returns ``None`` if no usable contracts (e.g. all expirations < ``min_dte``,
    or every contract is missing both an SDK-provided IV and a mid price for
    BS inversion).
    """
    front_expiration = _front_month(chain, as_of=as_of, min_dte=min_dte)
    if front_expiration is None:
        return None

    front_chain = [c for c in chain if c.expiration == front_expiration]
    nearest_strike = _strike_nearest_spot(front_chain, spot)
    if nearest_strike is None:
        return None

    atm_contracts = [c for c in front_chain if c.strike == nearest_strike]
    rate = risk_free_rate if risk_free_rate is not None else get_settings().risk_free_rate

    ivs: list[float] = []
    for contract in atm_contracts:
        iv = (
            contract.iv
            if contract.iv is not None
            else _iv_via_bs(contract, spot=spot, as_of=as_of, risk_free_rate=rate)
        )
        if iv is not None and iv > 0:
            ivs.append(iv)

    if not ivs:
        return None
    return sum(ivs) / len(ivs)


def compute_iv_rank(
    history: Sequence[float | None],
    current: float,
    *,
    min_history: int = DEFAULT_MIN_HISTORY,
) -> float | None:
    """``(current - 52w_low) / (52w_high - 52w_low)`` clamped to [0, 1]."""
    cleaned = [v for v in history if v is not None and v > 0]
    if len(cleaned) < min_history:
        return None
    lo = min(cleaned)
    hi = max(cleaned)
    if hi == lo:
        return 0.5
    raw = (current - lo) / (hi - lo)
    return max(0.0, min(1.0, raw))


def compute_iv_percentile(
    history: Sequence[float | None],
    current: float,
    *,
    min_history: int = DEFAULT_MIN_HISTORY,
) -> float | None:
    """Fraction of historical days strictly below ``current``."""
    cleaned = [v for v in history if v is not None and v > 0]
    if len(cleaned) < min_history:
        return None
    below = sum(1 for v in cleaned if v < current)
    return below / len(cleaned)


def _front_month(chain: list[OptionSnapshotRecord], *, as_of: date, min_dte: int) -> date | None:
    candidates = sorted({c.expiration for c in chain if (c.expiration - as_of).days >= min_dte})
    return candidates[0] if candidates else None


def _strike_nearest_spot(chain: list[OptionSnapshotRecord], spot: float) -> float | None:
    if not chain:
        return None
    strikes = sorted({c.strike for c in chain})
    return min(strikes, key=lambda k: abs(k - spot))


def _iv_via_bs(
    contract: OptionSnapshotRecord,
    *,
    spot: float,
    as_of: date,
    risk_free_rate: float,
) -> float | None:
    mid = _mid_price(contract)
    if mid is None or mid <= 0:
        return None
    dte_years = max((contract.expiration - as_of).days, 1) / 365.0
    flag = "c" if contract.option_type == "call" else "p"
    try:
        return float(
            implied_volatility(
                price=mid,
                S=spot,
                K=contract.strike,
                t=dte_years,
                r=risk_free_rate,
                flag=flag,
            )
        )
    except (ValueError, ZeroDivisionError) as exc:
        log.debug("iv.bs_inversion_failed", strike=contract.strike, reason=str(exc))
        return None


def _mid_price(contract: OptionSnapshotRecord) -> float | None:
    if contract.bid is not None and contract.ask is not None and contract.ask > 0:
        return (contract.bid + contract.ask) / 2.0
    return contract.last
