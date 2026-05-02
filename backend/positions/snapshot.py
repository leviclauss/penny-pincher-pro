"""Daily P&L + greeks snapshot for every open wheel position.

For each open option leg (short put or covered call), pulls the latest
underlying close from ``bars_daily`` and the matching strike row from
``options_snapshot`` (current-only — historical chains aren't stored), then
computes mark-to-market P&L, % of max profit, days-to-expiry, and the leg
delta. For ``long_shares`` legs without an open option, just snapshots the
underlying mark.

The function returns a ``SnapshotSummary`` so the scheduler job can persist
metrics into ``job_runs.result_json``. Rows always go into
``position_snapshots`` keyed by ``(position_id, snapshot_at)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.logging import get_logger
from core.time import utcnow
from db.models.market import BarDaily, OptionsSnapshot
from db.models.positions import Position, PositionLeg, PositionSnapshot
from positions.state_machine import (
    CONTRACT_MULTIPLIER,
    LEG_COVERED_CALL,
    LEG_SHARES,
    LEG_SHORT_PUT,
    OUTCOME_OPEN,
    STATE_COVERED_CALL,
    STATE_LONG_SHARES,
    STATE_SHORT_PUT,
)

log = get_logger(__name__)

OPEN_STATES = (STATE_SHORT_PUT, STATE_COVERED_CALL, STATE_LONG_SHARES)


@dataclass(frozen=True, slots=True)
class SnapshotSummary:
    positions_snapshotted: int
    snapshots_written: int
    skipped_no_underlying: int


def run_snapshot_pass(
    session: Session,
    *,
    as_of: date | None = None,
    snapshot_at: datetime | None = None,
) -> SnapshotSummary:
    """Snapshot every open position once. ``as_of`` defaults to today."""
    today = as_of or utcnow().date()
    snapshot_ts = snapshot_at or utcnow()

    positions = (
        session.execute(select(Position).where(Position.state.in_(OPEN_STATES))).scalars().all()
    )

    written = 0
    skipped = 0
    seen = 0
    for position in positions:
        seen += 1
        underlying_price = _latest_close(session, position.symbol)
        if underlying_price is None:
            skipped += 1
            log.warning("positions.snapshot.no_underlying", id=position.id, symbol=position.symbol)
            continue

        option_leg = _open_option_leg(session, position)
        shares_leg = (
            _open_shares_leg(session, position) if position.state != STATE_SHORT_PUT else None
        )

        if option_leg is not None:
            row = _snapshot_for_option(
                session,
                position=position,
                option_leg=option_leg,
                shares_leg=shares_leg,
                underlying_price=underlying_price,
                today=today,
                snapshot_at=snapshot_ts,
            )
        else:
            row = _snapshot_for_shares(
                session=session,
                position=position,
                shares_leg=shares_leg,
                underlying_price=underlying_price,
                snapshot_at=snapshot_ts,
            )
        if row is not None:
            written += 1

    log.info(
        "positions.snapshot.done",
        positions=seen,
        snapshots=written,
        skipped=skipped,
    )
    return SnapshotSummary(
        positions_snapshotted=seen, snapshots_written=written, skipped_no_underlying=skipped
    )


def _latest_close(session: Session, symbol: str) -> float | None:
    return session.execute(
        select(BarDaily.close)
        .where(BarDaily.symbol == symbol)
        .order_by(BarDaily.date.desc())
        .limit(1)
    ).scalar_one_or_none()


def _open_option_leg(session: Session, position: Position) -> PositionLeg | None:
    if position.state == STATE_SHORT_PUT:
        target = LEG_SHORT_PUT
    elif position.state == STATE_COVERED_CALL:
        target = LEG_COVERED_CALL
    else:
        return None
    return session.execute(
        select(PositionLeg).where(
            PositionLeg.position_id == position.id,
            PositionLeg.leg_type == target,
            PositionLeg.outcome == OUTCOME_OPEN,
        )
    ).scalar_one_or_none()


def _open_shares_leg(session: Session, position: Position) -> PositionLeg | None:
    return session.execute(
        select(PositionLeg).where(
            PositionLeg.position_id == position.id,
            PositionLeg.leg_type == LEG_SHARES,
            PositionLeg.outcome == OUTCOME_OPEN,
        )
    ).scalar_one_or_none()


def _option_chain_row(
    session: Session,
    *,
    symbol: str,
    expiration: date,
    strike: float,
    option_type: str,
) -> OptionsSnapshot | None:
    return session.execute(
        select(OptionsSnapshot).where(
            OptionsSnapshot.symbol == symbol,
            OptionsSnapshot.expiration == expiration,
            OptionsSnapshot.strike == strike,
            OptionsSnapshot.option_type == option_type,
        )
    ).scalar_one_or_none()


def _option_mid(row: OptionsSnapshot | None) -> float | None:
    if row is None:
        return None
    if row.bid is not None and row.ask is not None:
        return (row.bid + row.ask) / 2.0
    return row.last


def _snapshot_for_option(
    session: Session,
    *,
    position: Position,
    option_leg: PositionLeg,
    shares_leg: PositionLeg | None,
    underlying_price: float,
    today: date,
    snapshot_at: datetime,
) -> PositionSnapshot:
    if option_leg.expiration is None or option_leg.strike is None or option_leg.contracts is None:
        raise ValueError(f"option leg {option_leg.id} missing expiration/strike/contracts")

    option_type = "put" if option_leg.leg_type == LEG_SHORT_PUT else "call"
    chain_row = _option_chain_row(
        session,
        symbol=position.symbol,
        expiration=option_leg.expiration,
        strike=option_leg.strike,
        option_type=option_type,
    )
    option_mid = _option_mid(chain_row)
    delta = chain_row.delta if chain_row is not None else None

    unrealized_pnl: float | None = None
    pct_max_profit: float | None = None
    if option_mid is not None and option_leg.entry_price is not None:
        # Short option: P&L = (credit - mark) * contracts * 100; max profit = credit
        unrealized_pnl = (
            (option_leg.entry_price - option_mid) * option_leg.contracts * CONTRACT_MULTIPLIER
        )
        if option_leg.entry_price > 0:
            pct_max_profit = max(0.0, min(1.0, 1.0 - (option_mid / option_leg.entry_price)))

        if (
            shares_leg is not None
            and shares_leg.shares is not None
            and shares_leg.entry_price is not None
        ):
            # Roll the shares mark-to-market into the position's unrealized P&L.
            unrealized_pnl += (underlying_price - shares_leg.entry_price) * shares_leg.shares

    dte = (option_leg.expiration - today).days

    row = PositionSnapshot(
        position_id=position.id,
        snapshot_at=snapshot_at,
        underlying_price=underlying_price,
        option_mid=option_mid,
        unrealized_pnl=unrealized_pnl,
        pct_max_profit=pct_max_profit,
        delta=delta,
        dte=dte,
    )
    session.add(row)
    session.flush()
    return row


def _snapshot_for_shares(
    *,
    session: Session,
    position: Position,
    shares_leg: PositionLeg | None,
    underlying_price: float,
    snapshot_at: datetime,
) -> PositionSnapshot:
    unrealized_pnl: float | None = None
    if (
        shares_leg is not None
        and shares_leg.shares is not None
        and shares_leg.entry_price is not None
    ):
        unrealized_pnl = (underlying_price - shares_leg.entry_price) * shares_leg.shares

    row = PositionSnapshot(
        position_id=position.id,
        snapshot_at=snapshot_at,
        underlying_price=underlying_price,
        option_mid=None,
        unrealized_pnl=unrealized_pnl,
        pct_max_profit=None,
        delta=None,
        dte=None,
    )
    session.add(row)
    session.flush()
    return row
