"""Option pricing for the strategy backtest.

Two pricers share a small Protocol so the simulator can be parameterized:

- ``SyntheticPricer`` — Black-Scholes against a sigma estimate from
  ``indicators_daily``, with strikes snapped to a fixed grid ($0.50 /
  $1 / $2.50 / $5 by spot magnitude). This matches the original backtest
  behavior and works when ``options_historical`` is empty.

- ``RealChainPricer`` — reads ``options_historical`` (backfilled by
  ``ingestion.options_history``) and uses the actual close as the mid.
  Picks the nearest *available* expiration to ``dte_target`` and the
  available strike whose BS-implied delta is closest to target. Falls
  back to ``SyntheticPricer`` per-call when a row is missing.

Volatility estimate, in priority order, matches the synthetic pricer:

1. ``indicators_daily.iv_atm`` for the symbol on/near ``as_of``.
2. ``indicators_daily.hv_20`` — 20-day realized vol annualized.
3. Realized vol computed on the fly from the bar window in ``ctx.bars``.
4. Hard floor (``DEFAULT_FALLBACK_SIGMA``) so pricing never returns
   ``None`` on a stale-history symbol; the simulator logs and skips
   entries that land on the floor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from typing import Protocol

from py_vollib.black_scholes import black_scholes
from py_vollib.black_scholes.greeks.analytical import delta as bs_delta
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from core.logging import get_logger
from db.models.market import OptionsHistorical

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


# --------------------------------------------------------------------- #
# Pricer abstraction
# --------------------------------------------------------------------- #


class Pricer(Protocol):
    """Pricing + strike/expiration selection used by the strategy simulator.

    Two concrete implementations: ``SyntheticPricer`` (BS + grid-snapped
    strikes, no DB) and ``RealChainPricer`` (reads from
    ``options_historical``, falls back to synthetic per-call).
    """

    def select_expiration(self, *, symbol: str, as_of: date, dte_target: int) -> date: ...

    def price_option(
        self,
        *,
        symbol: str,
        as_of: date,
        option_type: str,
        spot: float,
        strike: float,
        expiration: date,
        sigma: float,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    ) -> OptionQuote: ...

    def select_put_strike(
        self,
        *,
        symbol: str,
        as_of: date,
        spot: float,
        target_delta: float,
        expiration: date,
        sigma: float,
        days_to_expiry: int,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    ) -> float: ...

    def select_call_strike(
        self,
        *,
        symbol: str,
        as_of: date,
        spot: float,
        cost_basis: float,
        target_delta: float,
        expiration: date,
        sigma: float,
        days_to_expiry: int,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    ) -> float: ...


def expiration_friday_near(day: date, dte_target: int) -> date:
    """Pick the Friday closest to ``day + dte_target`` (the synthetic default)."""
    target = day + timedelta(days=dte_target)
    offset = (4 - target.weekday()) % 7
    return target + timedelta(days=offset)


class SyntheticPricer:
    """The pre-Phase-2 behavior, packaged as a ``Pricer``.

    Stateless aside from configuration. Ignores ``symbol`` since it doesn't
    look anything up — every method is pure given its inputs.
    """

    def select_expiration(self, *, symbol: str, as_of: date, dte_target: int) -> date:
        _ = symbol
        return expiration_friday_near(as_of, dte_target)

    def price_option(
        self,
        *,
        symbol: str,
        as_of: date,
        option_type: str,
        spot: float,
        strike: float,
        expiration: date,
        sigma: float,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    ) -> OptionQuote:
        _ = symbol
        return price_option(
            option_type=option_type,
            spot=spot,
            strike=strike,
            as_of=as_of,
            expiration=expiration,
            sigma=sigma,
            risk_free_rate=risk_free_rate,
        )

    def select_put_strike(
        self,
        *,
        symbol: str,
        as_of: date,
        spot: float,
        target_delta: float,
        expiration: date,
        sigma: float,
        days_to_expiry: int,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    ) -> float:
        _ = (symbol, as_of, expiration)
        return select_put_strike(
            spot=spot,
            target_delta=target_delta,
            sigma=sigma,
            days_to_expiry=days_to_expiry,
            risk_free_rate=risk_free_rate,
        )

    def select_call_strike(
        self,
        *,
        symbol: str,
        as_of: date,
        spot: float,
        cost_basis: float,
        target_delta: float,
        expiration: date,
        sigma: float,
        days_to_expiry: int,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    ) -> float:
        _ = (symbol, as_of, expiration)
        return select_call_strike(
            spot=spot,
            cost_basis=cost_basis,
            target_delta=target_delta,
            sigma=sigma,
            days_to_expiry=days_to_expiry,
            risk_free_rate=risk_free_rate,
        )


class RealChainPricer:
    """Reads from ``options_historical`` when possible, synthetic when not.

    The pricer holds the SQLAlchemy ``Session`` so the simulator's three
    pricing call sites don't need to thread it through. One pricer instance
    is alive for the duration of one backtest run.
    """

    def __init__(self, session: Session, *, fallback: Pricer | None = None) -> None:
        self._session = session
        self._fallback: Pricer = fallback or SyntheticPricer()

    def select_expiration(self, *, symbol: str, as_of: date, dte_target: int) -> date:
        target = as_of + timedelta(days=dte_target)
        # Only look ahead — short legs get opened today and held forward.
        rows = (
            self._session.execute(
                select(distinct(OptionsHistorical.expiration))
                .where(OptionsHistorical.symbol == symbol)
                .where(OptionsHistorical.as_of == as_of)
                .where(OptionsHistorical.expiration >= as_of)
            )
            .scalars()
            .all()
        )
        if not rows:
            return self._fallback.select_expiration(
                symbol=symbol, as_of=as_of, dte_target=dte_target
            )
        return min(rows, key=lambda d: abs((d - target).days))

    def price_option(
        self,
        *,
        symbol: str,
        as_of: date,
        option_type: str,
        spot: float,
        strike: float,
        expiration: date,
        sigma: float,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    ) -> OptionQuote:
        kind = "call" if option_type == "c" else "put"
        row = self._session.execute(
            select(OptionsHistorical.close)
            .where(OptionsHistorical.symbol == symbol)
            .where(OptionsHistorical.as_of == as_of)
            .where(OptionsHistorical.expiration == expiration)
            .where(OptionsHistorical.strike == strike)
            .where(OptionsHistorical.option_type == kind)
        ).first()
        if row is None or row[0] is None:
            return self._fallback.price_option(
                symbol=symbol,
                as_of=as_of,
                option_type=option_type,
                spot=spot,
                strike=strike,
                expiration=expiration,
                sigma=sigma,
                risk_free_rate=risk_free_rate,
            )

        # Real mid + BS-derived delta against the same sigma the synthetic
        # pricer would use. Polygon Developer doesn't expose historical
        # greeks at this tier, so delta has to be computed.
        days = max((expiration - as_of).days, MIN_DAYS_TO_EXPIRY)
        sigma_clamped = max(MIN_SIGMA, min(MAX_SIGMA, float(sigma)))
        d = float(bs_delta(option_type, spot, strike, days / 365.0, risk_free_rate, sigma_clamped))
        return OptionQuote(
            option_type=option_type,
            spot=spot,
            strike=strike,
            expiration=expiration,
            as_of=as_of,
            sigma=sigma_clamped,
            risk_free_rate=risk_free_rate,
            mid=max(float(row[0]), 0.0),
            delta=d,
        )

    def select_put_strike(
        self,
        *,
        symbol: str,
        as_of: date,
        spot: float,
        target_delta: float,
        expiration: date,
        sigma: float,
        days_to_expiry: int,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    ) -> float:
        strikes = self._available_strikes(
            symbol=symbol, as_of=as_of, expiration=expiration, option_type="put"
        )
        if not strikes:
            return self._fallback.select_put_strike(
                symbol=symbol,
                as_of=as_of,
                spot=spot,
                target_delta=target_delta,
                expiration=expiration,
                sigma=sigma,
                days_to_expiry=days_to_expiry,
                risk_free_rate=risk_free_rate,
            )
        target = -abs(float(target_delta))
        return _strike_with_delta_nearest(
            strikes=strikes,
            option_type="p",
            spot=spot,
            target=target,
            sigma=sigma,
            days_to_expiry=days_to_expiry,
            risk_free_rate=risk_free_rate,
        )

    def select_call_strike(
        self,
        *,
        symbol: str,
        as_of: date,
        spot: float,
        cost_basis: float,
        target_delta: float,
        expiration: date,
        sigma: float,
        days_to_expiry: int,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    ) -> float:
        all_strikes = self._available_strikes(
            symbol=symbol, as_of=as_of, expiration=expiration, option_type="call"
        )
        floor = max(spot, cost_basis)
        eligible = [k for k in all_strikes if k >= floor]
        if not eligible:
            return self._fallback.select_call_strike(
                symbol=symbol,
                as_of=as_of,
                spot=spot,
                cost_basis=cost_basis,
                target_delta=target_delta,
                expiration=expiration,
                sigma=sigma,
                days_to_expiry=days_to_expiry,
                risk_free_rate=risk_free_rate,
            )
        target = abs(float(target_delta))
        return _strike_with_delta_nearest(
            strikes=eligible,
            option_type="c",
            spot=spot,
            target=target,
            sigma=sigma,
            days_to_expiry=days_to_expiry,
            risk_free_rate=risk_free_rate,
        )

    def _available_strikes(
        self, *, symbol: str, as_of: date, expiration: date, option_type: str
    ) -> list[float]:
        rows = (
            self._session.execute(
                select(distinct(OptionsHistorical.strike))
                .where(OptionsHistorical.symbol == symbol)
                .where(OptionsHistorical.as_of == as_of)
                .where(OptionsHistorical.expiration == expiration)
                .where(OptionsHistorical.option_type == option_type)
            )
            .scalars()
            .all()
        )
        return sorted(float(s) for s in rows)


def _strike_with_delta_nearest(
    *,
    strikes: list[float],
    option_type: str,
    spot: float,
    target: float,
    sigma: float,
    days_to_expiry: int,
    risk_free_rate: float,
) -> float:
    sigma_clamped = max(MIN_SIGMA, min(MAX_SIGMA, float(sigma)))
    t_years = max(days_to_expiry, MIN_DAYS_TO_EXPIRY) / 365.0
    best = strikes[0]
    best_diff = float("inf")
    for strike in strikes:
        d = float(bs_delta(option_type, spot, strike, t_years, risk_free_rate, sigma_clamped))
        diff = abs(d - target)
        if diff < best_diff:
            best_diff = diff
            best = strike
    return best
