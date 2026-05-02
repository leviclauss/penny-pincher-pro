"""Performance attribution for completed wheel cycles.

Per doc 04, the interesting metric isn't per-trade win rate but per-cycle
return on capital. For a closed position we sum every put/call leg's
realized P&L (premium collected, less buybacks), figure out the effective
cost basis (strike minus cumulative put credits per share) when assignment
happened, and compute a simple annualized return based on capital tied up
across days held.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models.positions import Position, PositionLeg
from positions.state_machine import (
    CONTRACT_MULTIPLIER,
    LEG_COVERED_CALL,
    LEG_SHARES,
    LEG_SHORT_PUT,
    OUTCOME_ASSIGNED,
)


@dataclass(frozen=True, slots=True)
class CycleAttribution:
    position_id: int
    symbol: str
    days_in_cycle: int | None
    total_premium_collected: float
    shares_pnl: float
    realized_pnl: float
    cost_basis_per_share: float | None
    capital_tied_up: float | None
    annualized_return: float | None
    was_assigned: bool


def _sum(items: list[float | None]) -> float:
    return sum(v for v in items if v is not None)


def _legs(session: Session, position_id: int) -> list[PositionLeg]:
    return list(
        session.execute(
            select(PositionLeg)
            .where(PositionLeg.position_id == position_id)
            .order_by(PositionLeg.id)
        )
        .scalars()
        .all()
    )


def attribute(session: Session, position_id: int) -> CycleAttribution | None:
    """Return per-cycle metrics for a position. ``None`` if it doesn't exist."""
    position = session.get(Position, position_id)
    if position is None:
        return None
    legs = _legs(session, position_id)
    return _attribute_from_legs(position, legs)


def _attribute_from_legs(position: Position, legs: list[PositionLeg]) -> CycleAttribution:
    option_legs = [leg for leg in legs if leg.leg_type in (LEG_SHORT_PUT, LEG_COVERED_CALL)]
    shares_legs = [leg for leg in legs if leg.leg_type == LEG_SHARES]

    total_premium = _sum([leg.realized_pnl for leg in option_legs])
    shares_pnl = _sum([leg.realized_pnl for leg in shares_legs])
    realized = total_premium + shares_pnl

    was_assigned = any(
        leg.leg_type == LEG_SHORT_PUT and leg.outcome == OUTCOME_ASSIGNED for leg in legs
    )

    cost_basis_per_share: float | None = None
    capital_tied_up: float | None = None
    if was_assigned and shares_legs:
        shares = shares_legs[0]
        if shares.entry_price is not None and shares.shares is not None and shares.shares > 0:
            put_premium_per_share = sum(
                ((leg.realized_pnl or 0.0) / (leg.contracts * CONTRACT_MULTIPLIER))
                for leg in option_legs
                if leg.leg_type == LEG_SHORT_PUT and leg.contracts
            )
            cost_basis_per_share = shares.entry_price - put_premium_per_share
            capital_tied_up = shares.entry_price * shares.shares
    elif option_legs:
        # Pure premium plays: capital = collateral required = sum(strike * 100 * contracts)
        capital_tied_up = (
            sum(
                (leg.strike or 0.0) * (leg.contracts or 0) * CONTRACT_MULTIPLIER
                for leg in option_legs
                if leg.leg_type == LEG_SHORT_PUT
            )
            or None
        )

    days = _days_in_cycle(position, legs)
    annualized = _annualized(realized, capital_tied_up, days)

    return CycleAttribution(
        position_id=position.id,
        symbol=position.symbol,
        days_in_cycle=days,
        total_premium_collected=total_premium,
        shares_pnl=shares_pnl,
        realized_pnl=realized,
        cost_basis_per_share=cost_basis_per_share,
        capital_tied_up=capital_tied_up,
        annualized_return=annualized,
        was_assigned=was_assigned,
    )


def _days_in_cycle(position: Position, legs: list[PositionLeg]) -> int | None:
    entry_dates = [leg.entry_date for leg in legs if leg.entry_date is not None]
    exit_dates = [leg.exit_date for leg in legs if leg.exit_date is not None]
    if not entry_dates:
        return None
    start = min(entry_dates)
    # Prefer the user-reported leg exit date over the position's wallclock
    # ``closed_at``: the latter is set with ``utcnow()`` at transition time,
    # which can lag the actual market close date by days when entries are
    # backdated.
    if exit_dates:
        end = max(exit_dates)
    elif position.closed_at is not None:
        end = position.closed_at.date()
    else:
        return None
    return max((end - start).days, 0)


def _annualized(realized: float, capital: float | None, days: int | None) -> float | None:
    if capital is None or capital <= 0 or days is None or days <= 0:
        return None
    return (realized / capital) * (365.0 / days)
