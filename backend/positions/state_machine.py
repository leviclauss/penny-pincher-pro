"""Wheel state machine: pure transition logic + leg P&L computation.

State graph (mirrors doc 04):

    None ──open_short_put──▶ short_put
                                 │
                ┌────────────────┼────────────────┐
        close_put│       expire_put│         assign_put│
                ▼                ▼                ▼
              closed           closed         long_shares
                                                  │
                                  ┌───────────────┼─────────────────┐
                       open_call  │  close_shares │                  │
                                  ▼               ▼                  ▼
                            covered_call       closed         (still long)
                                  │
                ┌─────────────────┼─────────────────┐
       close_call│       expire_call│      called_away│
                ▼                 ▼                 ▼
           long_shares       long_shares          closed

The functions here mutate ORM rows but do NOT commit — callers (the service /
API layer) own the session lifecycle. Each function validates the current
state, the leg invariants, and computes ``realized_pnl`` for any leg it
closes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as DateType

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.time import utcnow
from db.models.positions import Position, PositionLeg

STATE_SHORT_PUT = "short_put"
STATE_LONG_SHARES = "long_shares"
STATE_COVERED_CALL = "covered_call"
STATE_CLOSED = "closed"

LEG_SHORT_PUT = "short_put"
LEG_COVERED_CALL = "covered_call"
LEG_SHARES = "shares"

OUTCOME_OPEN = "open"
OUTCOME_CLOSED = "closed"
OUTCOME_EXPIRED = "expired"
OUTCOME_ASSIGNED = "assigned"
OUTCOME_CALLED_AWAY = "called_away"

ACQUISITION_OPEN_MARKET = "open_market"
ACQUISITION_ASSIGNMENT = "assignment"
_ALLOWED_ACQUISITION = {ACQUISITION_OPEN_MARKET, ACQUISITION_ASSIGNMENT}

CONTRACT_MULTIPLIER = 100


class PositionError(Exception):
    """Base exception for position-machine errors."""


class InvalidTransitionError(PositionError):
    """Action is not valid given the position's current state."""


class InvalidLegError(PositionError):
    """The expected leg for an action is missing or in the wrong state."""


@dataclass(frozen=True, slots=True)
class OpenShortPutInput:
    symbol: str
    expiration: DateType
    strike: float
    contracts: int
    credit: float
    opened_on: DateType
    fees: float = 0.0
    notes: str | None = None
    portfolio_id: int | None = None


@dataclass(frozen=True, slots=True)
class OpenCoveredCallInput:
    expiration: DateType
    strike: float
    contracts: int
    credit: float
    opened_on: DateType
    fees: float = 0.0


@dataclass(frozen=True, slots=True)
class OpenLongSharesInput:
    symbol: str
    shares: int
    cost_basis: float
    opened_on: DateType
    acquisition_source: str
    fees: float = 0.0
    notes: str | None = None
    portfolio_id: int | None = None


@dataclass(frozen=True, slots=True)
class OpenCoveredCallFreshInput:
    symbol: str
    shares: int
    cost_basis: float
    opened_on: DateType
    acquisition_source: str
    expiration: DateType
    strike: float
    contracts: int
    credit: float
    fees: float = 0.0
    notes: str | None = None
    portfolio_id: int | None = None


def open_short_put(session: Session, payload: OpenShortPutInput) -> Position:
    """Create a brand-new wheel cycle in ``short_put`` state."""
    if payload.contracts <= 0:
        raise InvalidLegError("contracts must be positive")
    if payload.credit <= 0:
        raise InvalidLegError("credit must be positive")
    if payload.expiration < payload.opened_on:
        raise InvalidLegError("expiration cannot precede opened_on")

    now = utcnow()
    position = Position(
        symbol=payload.symbol.upper(),
        state=STATE_SHORT_PUT,
        opened_at=now,
        notes=payload.notes,
        portfolio_id=payload.portfolio_id,
    )
    session.add(position)
    session.flush()
    position.cycle_id = position.id

    leg = PositionLeg(
        position_id=position.id,
        leg_type=LEG_SHORT_PUT,
        symbol=position.symbol,
        expiration=payload.expiration,
        strike=payload.strike,
        contracts=payload.contracts,
        shares=None,
        entry_price=payload.credit,
        entry_date=payload.opened_on,
        outcome=OUTCOME_OPEN,
        fees=payload.fees,
    )
    session.add(leg)
    session.flush()
    return position


def open_long_shares(session: Session, payload: OpenLongSharesInput) -> Position:
    """Create a wheel cycle that begins in ``long_shares`` (shares already held)."""
    if payload.shares <= 0:
        raise InvalidLegError("shares must be positive")
    if payload.cost_basis <= 0:
        raise InvalidLegError("cost_basis must be positive")
    if payload.acquisition_source not in _ALLOWED_ACQUISITION:
        raise InvalidLegError(f"acquisition_source must be one of {sorted(_ALLOWED_ACQUISITION)}")

    now = utcnow()
    position = Position(
        symbol=payload.symbol.upper(),
        state=STATE_LONG_SHARES,
        opened_at=now,
        notes=payload.notes,
        acquisition_source=payload.acquisition_source,
        portfolio_id=payload.portfolio_id,
    )
    session.add(position)
    session.flush()
    position.cycle_id = position.id

    leg = PositionLeg(
        position_id=position.id,
        leg_type=LEG_SHARES,
        symbol=position.symbol,
        shares=payload.shares,
        entry_price=payload.cost_basis,
        entry_date=payload.opened_on,
        outcome=OUTCOME_OPEN,
        fees=payload.fees,
    )
    session.add(leg)
    session.flush()
    return position


def open_covered_call_fresh(session: Session, payload: OpenCoveredCallFreshInput) -> Position:
    """Create a wheel cycle that begins in ``covered_call`` (shares + written call)."""
    if payload.shares <= 0:
        raise InvalidLegError("shares must be positive")
    if payload.cost_basis <= 0:
        raise InvalidLegError("cost_basis must be positive")
    if payload.contracts <= 0:
        raise InvalidLegError("contracts must be positive")
    if payload.credit <= 0:
        raise InvalidLegError("credit must be positive")
    if payload.expiration < payload.opened_on:
        raise InvalidLegError("expiration cannot precede opened_on")
    if payload.contracts * CONTRACT_MULTIPLIER > payload.shares:
        raise InvalidLegError("not enough shares to cover the call")
    if payload.acquisition_source not in _ALLOWED_ACQUISITION:
        raise InvalidLegError(f"acquisition_source must be one of {sorted(_ALLOWED_ACQUISITION)}")

    now = utcnow()
    position = Position(
        symbol=payload.symbol.upper(),
        state=STATE_COVERED_CALL,
        opened_at=now,
        notes=payload.notes,
        acquisition_source=payload.acquisition_source,
        portfolio_id=payload.portfolio_id,
    )
    session.add(position)
    session.flush()
    position.cycle_id = position.id

    shares_leg = PositionLeg(
        position_id=position.id,
        leg_type=LEG_SHARES,
        symbol=position.symbol,
        shares=payload.shares,
        entry_price=payload.cost_basis,
        entry_date=payload.opened_on,
        outcome=OUTCOME_OPEN,
        fees=payload.fees,
    )
    session.add(shares_leg)

    call_leg = PositionLeg(
        position_id=position.id,
        leg_type=LEG_COVERED_CALL,
        symbol=position.symbol,
        expiration=payload.expiration,
        strike=payload.strike,
        contracts=payload.contracts,
        entry_price=payload.credit,
        entry_date=payload.opened_on,
        outcome=OUTCOME_OPEN,
        fees=0.0,
    )
    session.add(call_leg)
    session.flush()
    return position


def close_short_put(
    session: Session,
    position_id: int,
    *,
    debit: float,
    closed_on: DateType,
    fees: float = 0.0,
) -> Position:
    """Buy back the short put for a debit; cycle ends."""
    position = _require_position(session, position_id)
    if position.state != STATE_SHORT_PUT:
        raise InvalidTransitionError(
            f"close_short_put requires state={STATE_SHORT_PUT}, got {position.state}"
        )
    if debit < 0:
        raise InvalidLegError("debit cannot be negative")

    leg = _require_open_leg(session, position_id, LEG_SHORT_PUT)
    leg.exit_price = debit
    leg.exit_date = closed_on
    leg.outcome = OUTCOME_CLOSED
    leg.realized_pnl = _option_credit_pnl(
        entry=leg.entry_price, exit_=debit, contracts=leg.contracts, fees=leg.fees + fees
    )
    leg.fees = leg.fees + fees

    _close_position(position)
    return position


def expire_short_put(
    session: Session,
    position_id: int,
    *,
    expired_on: DateType,
) -> Position:
    """Short put expired worthless; cycle ends with full premium kept."""
    position = _require_position(session, position_id)
    if position.state != STATE_SHORT_PUT:
        raise InvalidTransitionError(
            f"expire_short_put requires state={STATE_SHORT_PUT}, got {position.state}"
        )
    leg = _require_open_leg(session, position_id, LEG_SHORT_PUT)
    leg.exit_price = 0.0
    leg.exit_date = expired_on
    leg.outcome = OUTCOME_EXPIRED
    leg.realized_pnl = _option_credit_pnl(
        entry=leg.entry_price, exit_=0.0, contracts=leg.contracts, fees=leg.fees
    )
    _close_position(position)
    return position


def assign_short_put(
    session: Session,
    position_id: int,
    *,
    assigned_on: DateType,
) -> Position:
    """Put assigned: keep premium, take 100x shares per contract at strike."""
    position = _require_position(session, position_id)
    if position.state != STATE_SHORT_PUT:
        raise InvalidTransitionError(
            f"assign_short_put requires state={STATE_SHORT_PUT}, got {position.state}"
        )
    leg = _require_open_leg(session, position_id, LEG_SHORT_PUT)
    if leg.strike is None or leg.contracts is None or leg.entry_price is None:
        raise InvalidLegError("short put leg missing strike/contracts/entry")

    leg.exit_price = leg.entry_price
    leg.exit_date = assigned_on
    leg.outcome = OUTCOME_ASSIGNED
    leg.realized_pnl = _option_credit_pnl(
        entry=leg.entry_price, exit_=0.0, contracts=leg.contracts, fees=leg.fees
    )

    shares_leg = PositionLeg(
        position_id=position.id,
        leg_type=LEG_SHARES,
        symbol=position.symbol,
        shares=leg.contracts * CONTRACT_MULTIPLIER,
        entry_price=leg.strike,
        entry_date=assigned_on,
        outcome=OUTCOME_OPEN,
        fees=0.0,
    )
    session.add(shares_leg)
    position.state = STATE_LONG_SHARES
    session.flush()
    return position


def open_covered_call(
    session: Session,
    position_id: int,
    payload: OpenCoveredCallInput,
) -> Position:
    """From ``long_shares``, sell a covered call against the held shares."""
    position = _require_position(session, position_id)
    if position.state != STATE_LONG_SHARES:
        raise InvalidTransitionError(
            f"open_covered_call requires state={STATE_LONG_SHARES}, got {position.state}"
        )
    if payload.contracts <= 0:
        raise InvalidLegError("contracts must be positive")
    if payload.credit <= 0:
        raise InvalidLegError("credit must be positive")
    if payload.expiration < payload.opened_on:
        raise InvalidLegError("expiration cannot precede opened_on")

    shares_leg = _require_open_leg(session, position_id, LEG_SHARES)
    if shares_leg.shares is None:
        raise InvalidLegError("shares leg has no share count")
    if payload.contracts * CONTRACT_MULTIPLIER > shares_leg.shares:
        raise InvalidLegError("not enough shares to cover the call")

    leg = PositionLeg(
        position_id=position.id,
        leg_type=LEG_COVERED_CALL,
        symbol=position.symbol,
        expiration=payload.expiration,
        strike=payload.strike,
        contracts=payload.contracts,
        entry_price=payload.credit,
        entry_date=payload.opened_on,
        outcome=OUTCOME_OPEN,
        fees=payload.fees,
    )
    session.add(leg)
    position.state = STATE_COVERED_CALL
    session.flush()
    return position


def close_covered_call(
    session: Session,
    position_id: int,
    *,
    debit: float,
    closed_on: DateType,
    fees: float = 0.0,
) -> Position:
    """Buy back the covered call; back to ``long_shares``."""
    position = _require_position(session, position_id)
    if position.state != STATE_COVERED_CALL:
        raise InvalidTransitionError(
            f"close_covered_call requires state={STATE_COVERED_CALL}, got {position.state}"
        )
    if debit < 0:
        raise InvalidLegError("debit cannot be negative")
    leg = _require_open_leg(session, position_id, LEG_COVERED_CALL)
    leg.exit_price = debit
    leg.exit_date = closed_on
    leg.outcome = OUTCOME_CLOSED
    leg.realized_pnl = _option_credit_pnl(
        entry=leg.entry_price, exit_=debit, contracts=leg.contracts, fees=leg.fees + fees
    )
    leg.fees = leg.fees + fees
    position.state = STATE_LONG_SHARES
    session.flush()
    return position


def expire_covered_call(
    session: Session,
    position_id: int,
    *,
    expired_on: DateType,
) -> Position:
    """Covered call expired worthless; back to ``long_shares`` with premium kept."""
    position = _require_position(session, position_id)
    if position.state != STATE_COVERED_CALL:
        raise InvalidTransitionError(
            f"expire_covered_call requires state={STATE_COVERED_CALL}, got {position.state}"
        )
    leg = _require_open_leg(session, position_id, LEG_COVERED_CALL)
    leg.exit_price = 0.0
    leg.exit_date = expired_on
    leg.outcome = OUTCOME_EXPIRED
    leg.realized_pnl = _option_credit_pnl(
        entry=leg.entry_price, exit_=0.0, contracts=leg.contracts, fees=leg.fees
    )
    position.state = STATE_LONG_SHARES
    session.flush()
    return position


def called_away(
    session: Session,
    position_id: int,
    *,
    called_on: DateType,
) -> Position:
    """Call assigned: shares sold at strike, cycle ends."""
    position = _require_position(session, position_id)
    if position.state != STATE_COVERED_CALL:
        raise InvalidTransitionError(
            f"called_away requires state={STATE_COVERED_CALL}, got {position.state}"
        )
    call_leg = _require_open_leg(session, position_id, LEG_COVERED_CALL)
    if call_leg.strike is None or call_leg.contracts is None or call_leg.entry_price is None:
        raise InvalidLegError("covered call leg missing strike/contracts/entry")
    shares_leg = _require_open_leg(session, position_id, LEG_SHARES)
    if shares_leg.shares is None or shares_leg.entry_price is None:
        raise InvalidLegError("shares leg missing shares/entry")

    call_leg.exit_price = call_leg.entry_price
    call_leg.exit_date = called_on
    call_leg.outcome = OUTCOME_CALLED_AWAY
    call_leg.realized_pnl = _option_credit_pnl(
        entry=call_leg.entry_price,
        exit_=0.0,
        contracts=call_leg.contracts,
        fees=call_leg.fees,
    )

    shares_leg.exit_price = call_leg.strike
    shares_leg.exit_date = called_on
    shares_leg.outcome = OUTCOME_CALLED_AWAY
    shares_leg.realized_pnl = (call_leg.strike - shares_leg.entry_price) * shares_leg.shares - (
        shares_leg.fees
    )

    _close_position(position)
    return position


def close_shares_manual(
    session: Session,
    position_id: int,
    *,
    sale_price: float,
    closed_on: DateType,
    fees: float = 0.0,
) -> Position:
    """Manually sell the shares without an open covered call (cycle ends)."""
    position = _require_position(session, position_id)
    if position.state != STATE_LONG_SHARES:
        raise InvalidTransitionError(
            f"close_shares_manual requires state={STATE_LONG_SHARES}, got {position.state}"
        )
    if sale_price < 0:
        raise InvalidLegError("sale_price cannot be negative")

    shares_leg = _require_open_leg(session, position_id, LEG_SHARES)
    if shares_leg.shares is None or shares_leg.entry_price is None:
        raise InvalidLegError("shares leg missing shares/entry")

    shares_leg.exit_price = sale_price
    shares_leg.exit_date = closed_on
    shares_leg.outcome = OUTCOME_CLOSED
    shares_leg.fees = shares_leg.fees + fees
    shares_leg.realized_pnl = (sale_price - shares_leg.entry_price) * shares_leg.shares - (
        shares_leg.fees
    )

    _close_position(position)
    return position


def _require_position(session: Session, position_id: int) -> Position:
    position = session.get(Position, position_id)
    if position is None:
        raise PositionError(f"position {position_id} not found")
    return position


def _require_open_leg(session: Session, position_id: int, leg_type: str) -> PositionLeg:
    leg = session.execute(
        select(PositionLeg).where(
            PositionLeg.position_id == position_id,
            PositionLeg.leg_type == leg_type,
            PositionLeg.outcome == OUTCOME_OPEN,
        )
    ).scalar_one_or_none()
    if leg is None:
        raise InvalidLegError(f"no open {leg_type} leg on position {position_id}")
    return leg


def _option_credit_pnl(
    *, entry: float | None, exit_: float, contracts: int | None, fees: float
) -> float:
    if entry is None or contracts is None:
        raise InvalidLegError("option leg missing entry or contracts")
    return (entry - exit_) * contracts * CONTRACT_MULTIPLIER - fees


def _close_position(position: Position) -> None:
    position.state = STATE_CLOSED
    position.closed_at = utcnow()
