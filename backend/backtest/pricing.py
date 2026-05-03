"""Synthetic option pricing for the strategy backtest.

Historical chains aren't stored (``options_snapshot`` is current-only per
CLAUDE.md), so the simulator prices contracts via Black-Scholes against an
estimate of implied volatility derived from the same point-in-time data the
screener used.

Volatility estimate, in priority order:

1. ``indicators_daily.iv_atm`` for the symbol on/near ``as_of`` — populated
   when an options snapshot existed during ingestion.
2. ``indicators_daily.hv_20`` — 20-day realized vol annualized.
3. Realized vol computed on the fly from the bar window in ``ctx.bars``.
4. Hard floor (``DEFAULT_FALLBACK_SIGMA``) so pricing never returns ``None``
   on a stale-history symbol; the simulator logs and skips entries that
   land on the floor.

Strikes follow a fixed grid (``$0.50`` / ``$1`` / ``$2.50`` / ``$5`` based
on spot magnitude) so the chosen strike is realistic rather than the exact
solution to ``N(d2) == target_delta``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

from py_vollib.black_scholes import black_scholes
from py_vollib.black_scholes.greeks.analytical import delta as bs_delta

from core.logging import get_logger

log = get_logger(__name__)

DEFAULT_RISK_FREE_RATE = 0.045
DEFAULT_FALLBACK_SIGMA = 0.30
MIN_SIGMA = 0.05
MAX_SIGMA = 3.0
MIN_DAYS_TO_EXPIRY = 1
TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True, slots=True)
class OptionQuote:
    """A single synthetic option price with the inputs that produced it."""

    option_type: str  # "p" or "c"
    spot: float
    strike: float
    expiration: date
    as_of: date
    sigma: float
    risk_free_rate: float
    mid: float
    delta: float

    @property
    def days_to_expiry(self) -> int:
        return max((self.expiration - self.as_of).days, MIN_DAYS_TO_EXPIRY)


def price_option(
    *,
    option_type: str,
    spot: float,
    strike: float,
    as_of: date,
    expiration: date,
    sigma: float,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> OptionQuote:
    """Black-Scholes price + delta for one contract.

    ``sigma`` is annualized volatility (e.g. 0.30 = 30%). Time is measured in
    calendar years to expiry, floored at one day so a same-day expiration is
    still priceable as deep intrinsic value.
    """
    if option_type not in ("p", "c"):
        raise ValueError(f"option_type must be 'p' or 'c', got {option_type!r}")
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must be positive")
    sigma_clamped = max(MIN_SIGMA, min(MAX_SIGMA, float(sigma)))
    days = max((expiration - as_of).days, MIN_DAYS_TO_EXPIRY)
    t_years = days / 365.0
    price = float(black_scholes(option_type, spot, strike, t_years, risk_free_rate, sigma_clamped))
    d = float(bs_delta(option_type, spot, strike, t_years, risk_free_rate, sigma_clamped))
    return OptionQuote(
        option_type=option_type,
        spot=spot,
        strike=strike,
        expiration=expiration,
        as_of=as_of,
        sigma=sigma_clamped,
        risk_free_rate=risk_free_rate,
        mid=max(price, 0.0),
        delta=d,
    )


def estimate_sigma(
    *,
    iv_atm: float | None,
    hv_20: float | None,
    realized_fallback: float | None,
) -> float:
    """Pick the best available annualized vol estimate.

    Returns ``DEFAULT_FALLBACK_SIGMA`` rather than ``None`` so option pricing
    is always defined; callers needing strict point-in-time IV should check
    the inputs themselves first.
    """
    for candidate in (iv_atm, hv_20, realized_fallback):
        if candidate is not None and candidate > 0:
            return float(candidate)
    return DEFAULT_FALLBACK_SIGMA


def realized_vol_from_closes(closes: list[float], window: int = 20) -> float | None:
    """Annualized stdev of log returns over the trailing ``window`` closes."""
    if len(closes) < window + 1:
        return None
    tail = closes[-(window + 1) :]
    log_returns: list[float] = []
    for prev, cur in pairwise(tail):
        if prev <= 0 or cur <= 0:
            return None
        log_returns.append(math.log(cur / prev))
    if not log_returns:
        return None
    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / max(len(log_returns) - 1, 1)
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)


def select_put_strike(
    *,
    spot: float,
    target_delta: float,
    sigma: float,
    days_to_expiry: int,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> float:
    """Pick the strike on the standard grid whose put delta is closest to ``-|target_delta|``.

    ``target_delta`` is given as a positive magnitude (e.g. 0.30); the actual
    put delta is negative. We search a window around spot and snap to the
    realistic strike grid for the spot price.
    """
    if spot <= 0:
        raise ValueError("spot must be positive")
    target = -abs(float(target_delta))
    grid_step = _strike_grid_step(spot)
    # Wide enough window to cover deep OTM puts even at low vols.
    lo = spot * 0.50
    hi = spot * 1.05
    candidates = _strike_grid(lo, hi, grid_step)
    if not candidates:
        return _round_to_grid(spot, grid_step)

    t_years = max(days_to_expiry, MIN_DAYS_TO_EXPIRY) / 365.0
    sigma_clamped = max(MIN_SIGMA, min(MAX_SIGMA, sigma))

    best_strike = candidates[0]
    best_diff = float("inf")
    for strike in candidates:
        d = float(bs_delta("p", spot, strike, t_years, risk_free_rate, sigma_clamped))
        diff = abs(d - target)
        if diff < best_diff:
            best_diff = diff
            best_strike = strike
    return best_strike


def select_call_strike(
    *,
    spot: float,
    cost_basis: float,
    target_delta: float,
    sigma: float,
    days_to_expiry: int,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> float:
    """Pick a call strike at-or-above ``cost_basis`` and as close to ``target_delta`` as possible.

    The wheel sells calls *above the share cost basis* so an assignment locks
    in a profit on the underlying. We search OTM only (strike >= spot AND
    strike >= cost_basis) and pick the strike whose call delta is nearest
    ``+target_delta``.
    """
    if spot <= 0:
        raise ValueError("spot must be positive")
    target = abs(float(target_delta))
    grid_step = _strike_grid_step(spot)
    floor_strike = max(spot, cost_basis)
    lo = _round_to_grid(floor_strike, grid_step)
    if lo < floor_strike:
        lo += grid_step
    hi = spot * 1.50
    candidates = _strike_grid(lo, hi, grid_step)
    if not candidates:
        return lo

    t_years = max(days_to_expiry, MIN_DAYS_TO_EXPIRY) / 365.0
    sigma_clamped = max(MIN_SIGMA, min(MAX_SIGMA, sigma))

    best_strike = candidates[0]
    best_diff = float("inf")
    for strike in candidates:
        d = float(bs_delta("c", spot, strike, t_years, risk_free_rate, sigma_clamped))
        diff = abs(d - target)
        if diff < best_diff:
            best_diff = diff
            best_strike = strike
    return best_strike


def _strike_grid_step(spot: float) -> float:
    if spot < 25:
        return 0.5
    if spot < 100:
        return 1.0
    if spot < 250:
        return 2.5
    return 5.0


def _strike_grid(lo: float, hi: float, step: float) -> list[float]:
    if hi < lo or step <= 0:
        return []
    start = _round_to_grid(lo, step)
    if start < lo:
        start += step
    out: list[float] = []
    cur = start
    # Cap iteration to a sane upper bound to avoid pathological loops.
    for _ in range(2000):
        if cur > hi:
            break
        out.append(round(cur, 4))
        cur += step
    return out


def _round_to_grid(value: float, step: float) -> float:
    return round(value / step) * step
