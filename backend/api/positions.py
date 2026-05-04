"""Positions resource: list, detail, manual entry + state-machine transitions.

The API is the public face of ``positions.state_machine``. Each transition is
its own POST endpoint (matching the verbs in doc 04) so the UI can render a
distinct button per action and surface the right input fields.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.logging import get_logger
from db import get_session
from db.models.positions import Portfolio, Position, PositionLeg, PositionSnapshot
from positions import state_machine as sm
from positions.attribution import attribute

log = get_logger(__name__)

router = APIRouter(prefix="/api/positions", tags=["positions"])


class LegOut(BaseModel):
    id: int
    leg_type: str
    symbol: str
    expiration: date | None
    strike: float | None
    contracts: int | None
    shares: int | None
    entry_price: float | None
    exit_price: float | None
    entry_date: date | None
    exit_date: date | None
    outcome: str | None
    realized_pnl: float | None
    fees: float


class SnapshotOut(BaseModel):
    snapshot_at: datetime
    underlying_price: float | None
    option_mid: float | None
    unrealized_pnl: float | None
    pct_max_profit: float | None
    delta: float | None
    dte: int | None


class PositionOut(BaseModel):
    id: int
    symbol: str
    state: str
    cycle_id: int | None
    opened_at: datetime
    closed_at: datetime | None
    notes: str | None
    acquisition_source: str | None
    portfolio_id: int | None
    legs: list[LegOut]
    latest_snapshot: SnapshotOut | None


AcquisitionSource = Literal["open_market", "assignment"]


class OpenShortPutBody(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16)
    expiration: date
    strike: float = Field(..., gt=0)
    contracts: int = Field(..., gt=0)
    credit: float = Field(..., gt=0)
    opened_on: date
    fees: float = Field(default=0.0, ge=0)
    notes: str | None = None
    portfolio_id: int | None = None


class OpenLongSharesBody(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16)
    shares: int = Field(..., gt=0)
    cost_basis: float = Field(..., gt=0)
    opened_on: date
    acquisition_source: AcquisitionSource
    fees: float = Field(default=0.0, ge=0)
    notes: str | None = None
    portfolio_id: int | None = None


class OpenCoveredCallFreshBody(OpenLongSharesBody):
    expiration: date
    strike: float = Field(..., gt=0)
    contracts: int = Field(..., gt=0)
    credit: float = Field(..., gt=0)


class CloseDebitBody(BaseModel):
    debit: float = Field(..., ge=0)
    closed_on: date
    fees: float = Field(default=0.0, ge=0)


class ExpireBody(BaseModel):
    expired_on: date


class AssignBody(BaseModel):
    assigned_on: date


class CalledAwayBody(BaseModel):
    called_on: date


class OpenCoveredCallBody(BaseModel):
    expiration: date
    strike: float = Field(..., gt=0)
    contracts: int = Field(..., gt=0)
    credit: float = Field(..., gt=0)
    opened_on: date
    fees: float = Field(default=0.0, ge=0)


class CloseSharesBody(BaseModel):
    sale_price: float = Field(..., ge=0)
    closed_on: date
    fees: float = Field(default=0.0, ge=0)


class PatchPositionBody(BaseModel):
    notes: str | None = None
    portfolio_id: int | None = None


class AttributionOut(BaseModel):
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


def _leg_to_out(leg: PositionLeg) -> LegOut:
    return LegOut(
        id=leg.id,
        leg_type=leg.leg_type,
        symbol=leg.symbol,
        expiration=leg.expiration,
        strike=leg.strike,
        contracts=leg.contracts,
        shares=leg.shares,
        entry_price=leg.entry_price,
        exit_price=leg.exit_price,
        entry_date=leg.entry_date,
        exit_date=leg.exit_date,
        outcome=leg.outcome,
        realized_pnl=leg.realized_pnl,
        fees=leg.fees,
    )


def _snapshot_to_out(row: PositionSnapshot) -> SnapshotOut:
    return SnapshotOut(
        snapshot_at=row.snapshot_at,
        underlying_price=row.underlying_price,
        option_mid=row.option_mid,
        unrealized_pnl=row.unrealized_pnl,
        pct_max_profit=row.pct_max_profit,
        delta=row.delta,
        dte=row.dte,
    )


def _load(position_id: int) -> PositionOut:
    with get_session() as session:
        position = session.get(Position, position_id)
        if position is None:
            raise HTTPException(status_code=404, detail=f"position {position_id} not found")
        # detach a copy; subsequent helpers re-open a session
        out = PositionOut(
            id=position.id,
            symbol=position.symbol,
            state=position.state,
            cycle_id=position.cycle_id,
            opened_at=position.opened_at,
            closed_at=position.closed_at,
            notes=position.notes,
            acquisition_source=position.acquisition_source,
            portfolio_id=position.portfolio_id,
            legs=[],
            latest_snapshot=None,
        )
        legs = (
            session.execute(
                select(PositionLeg)
                .where(PositionLeg.position_id == position.id)
                .order_by(PositionLeg.id)
            )
            .scalars()
            .all()
        )
        out.legs = [_leg_to_out(leg) for leg in legs]
        snapshot = session.execute(
            select(PositionSnapshot)
            .where(PositionSnapshot.position_id == position.id)
            .order_by(PositionSnapshot.snapshot_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if snapshot is not None:
            out.latest_snapshot = _snapshot_to_out(snapshot)
    return out


def _run_transition(position_id: int, body: object, fn_name: str) -> PositionOut:
    """Adapter: run a state_machine function inside a session, then hydrate."""
    try:
        with get_session() as session:
            _dispatch_transition(session, position_id, body, fn_name)
            session.flush()
    except sm.InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except sm.InvalidLegError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except sm.PositionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    log.info("positions.transition", id=position_id, action=fn_name)
    return _load(position_id)


def _dispatch_transition(session: Session, position_id: int, body: object, fn_name: str) -> None:
    if fn_name == "close_short_put":
        assert isinstance(body, CloseDebitBody)
        sm.close_short_put(
            session, position_id, debit=body.debit, closed_on=body.closed_on, fees=body.fees
        )
    elif fn_name == "expire_short_put":
        assert isinstance(body, ExpireBody)
        sm.expire_short_put(session, position_id, expired_on=body.expired_on)
    elif fn_name == "assign_short_put":
        assert isinstance(body, AssignBody)
        sm.assign_short_put(session, position_id, assigned_on=body.assigned_on)
    elif fn_name == "open_covered_call":
        assert isinstance(body, OpenCoveredCallBody)
        sm.open_covered_call(
            session,
            position_id,
            sm.OpenCoveredCallInput(
                expiration=body.expiration,
                strike=body.strike,
                contracts=body.contracts,
                credit=body.credit,
                opened_on=body.opened_on,
                fees=body.fees,
            ),
        )
    elif fn_name == "close_covered_call":
        assert isinstance(body, CloseDebitBody)
        sm.close_covered_call(
            session, position_id, debit=body.debit, closed_on=body.closed_on, fees=body.fees
        )
    elif fn_name == "expire_covered_call":
        assert isinstance(body, ExpireBody)
        sm.expire_covered_call(session, position_id, expired_on=body.expired_on)
    elif fn_name == "called_away":
        assert isinstance(body, CalledAwayBody)
        sm.called_away(session, position_id, called_on=body.called_on)
    elif fn_name == "close_shares":
        assert isinstance(body, CloseSharesBody)
        sm.close_shares_manual(
            session,
            position_id,
            sale_price=body.sale_price,
            closed_on=body.closed_on,
            fees=body.fees,
        )
    else:  # pragma: no cover — guard for typo'd dispatch
        raise RuntimeError(f"unknown transition: {fn_name}")


StateFilter = Literal["short_put", "long_shares", "covered_call", "closed"]

_STATE_QUERY = Query(default=None)
_SYMBOL_QUERY = Query(default=None)
_PORTFOLIO_QUERY = Query(default=None)


@router.get("", response_model=list[PositionOut])
def list_positions(
    state: StateFilter | None = _STATE_QUERY,
    symbol: str | None = _SYMBOL_QUERY,
    portfolio_id: int | None = _PORTFOLIO_QUERY,
) -> list[PositionOut]:
    """All positions, optionally filtered by ``state``, ``symbol`` or ``portfolio_id``.

    ``portfolio_id=0`` selects positions with no portfolio assigned (we use 0
    as the sentinel because ``None`` means "don't filter").
    """
    with get_session() as session:
        stmt = select(Position).order_by(Position.opened_at.desc())
        if state is not None:
            stmt = stmt.where(Position.state == state)
        if symbol is not None:
            stmt = stmt.where(Position.symbol == symbol.upper())
        if portfolio_id is not None:
            if portfolio_id == 0:
                stmt = stmt.where(Position.portfolio_id.is_(None))
            else:
                stmt = stmt.where(Position.portfolio_id == portfolio_id)
        positions = session.execute(stmt).scalars().all()
        ids = [p.id for p in positions]

        legs_by_pos: dict[int, list[PositionLeg]] = {pid: [] for pid in ids}
        if ids:
            for leg in (
                session.execute(
                    select(PositionLeg)
                    .where(PositionLeg.position_id.in_(ids))
                    .order_by(PositionLeg.position_id, PositionLeg.id)
                )
                .scalars()
                .all()
            ):
                legs_by_pos.setdefault(leg.position_id, []).append(leg)

    return [
        PositionOut(
            id=p.id,
            symbol=p.symbol,
            state=p.state,
            cycle_id=p.cycle_id,
            opened_at=p.opened_at,
            closed_at=p.closed_at,
            notes=p.notes,
            acquisition_source=p.acquisition_source,
            portfolio_id=p.portfolio_id,
            legs=[_leg_to_out(leg) for leg in legs_by_pos.get(p.id, [])],
            latest_snapshot=None,
        )
        for p in positions
    ]


@router.get("/{position_id}", response_model=PositionOut)
def get_position(position_id: int) -> PositionOut:
    return _load(position_id)


@router.post("/short-put", response_model=PositionOut, status_code=201)
def open_short_put_endpoint(body: OpenShortPutBody) -> PositionOut:
    try:
        with get_session() as session:
            _require_portfolio_exists(session, body.portfolio_id)
            position = sm.open_short_put(
                session,
                sm.OpenShortPutInput(
                    symbol=body.symbol,
                    expiration=body.expiration,
                    strike=body.strike,
                    contracts=body.contracts,
                    credit=body.credit,
                    opened_on=body.opened_on,
                    fees=body.fees,
                    notes=body.notes,
                    portfolio_id=body.portfolio_id,
                ),
            )
            session.flush()
            position_id = position.id
    except sm.InvalidLegError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    log.info("positions.open_short_put", id=position_id, symbol=body.symbol.upper())
    return _load(position_id)


@router.post("/long-shares", response_model=PositionOut, status_code=201)
def open_long_shares_endpoint(body: OpenLongSharesBody) -> PositionOut:
    try:
        with get_session() as session:
            _require_portfolio_exists(session, body.portfolio_id)
            position = sm.open_long_shares(
                session,
                sm.OpenLongSharesInput(
                    symbol=body.symbol,
                    shares=body.shares,
                    cost_basis=body.cost_basis,
                    opened_on=body.opened_on,
                    acquisition_source=body.acquisition_source,
                    fees=body.fees,
                    notes=body.notes,
                    portfolio_id=body.portfolio_id,
                ),
            )
            session.flush()
            position_id = position.id
    except sm.InvalidLegError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    log.info(
        "positions.open_long_shares",
        id=position_id,
        symbol=body.symbol.upper(),
        acquisition_source=body.acquisition_source,
    )
    return _load(position_id)


@router.post("/covered-call", response_model=PositionOut, status_code=201)
def open_covered_call_fresh_endpoint(body: OpenCoveredCallFreshBody) -> PositionOut:
    try:
        with get_session() as session:
            _require_portfolio_exists(session, body.portfolio_id)
            position = sm.open_covered_call_fresh(
                session,
                sm.OpenCoveredCallFreshInput(
                    symbol=body.symbol,
                    shares=body.shares,
                    cost_basis=body.cost_basis,
                    opened_on=body.opened_on,
                    acquisition_source=body.acquisition_source,
                    expiration=body.expiration,
                    strike=body.strike,
                    contracts=body.contracts,
                    credit=body.credit,
                    fees=body.fees,
                    notes=body.notes,
                    portfolio_id=body.portfolio_id,
                ),
            )
            session.flush()
            position_id = position.id
    except sm.InvalidLegError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    log.info(
        "positions.open_covered_call_fresh",
        id=position_id,
        symbol=body.symbol.upper(),
        acquisition_source=body.acquisition_source,
    )
    return _load(position_id)


@router.post("/{position_id}/close-put", response_model=PositionOut)
def close_put(position_id: int, body: CloseDebitBody) -> PositionOut:
    return _run_transition(position_id, body, "close_short_put")


@router.post("/{position_id}/expire-put", response_model=PositionOut)
def expire_put(position_id: int, body: ExpireBody) -> PositionOut:
    return _run_transition(position_id, body, "expire_short_put")


@router.post("/{position_id}/assign-put", response_model=PositionOut)
def assign_put(position_id: int, body: AssignBody) -> PositionOut:
    return _run_transition(position_id, body, "assign_short_put")


@router.post("/{position_id}/covered-call", response_model=PositionOut)
def covered_call(position_id: int, body: OpenCoveredCallBody) -> PositionOut:
    return _run_transition(position_id, body, "open_covered_call")


@router.post("/{position_id}/close-call", response_model=PositionOut)
def close_call(position_id: int, body: CloseDebitBody) -> PositionOut:
    return _run_transition(position_id, body, "close_covered_call")


@router.post("/{position_id}/expire-call", response_model=PositionOut)
def expire_call(position_id: int, body: ExpireBody) -> PositionOut:
    return _run_transition(position_id, body, "expire_covered_call")


@router.post("/{position_id}/called-away", response_model=PositionOut)
def called_away_endpoint(position_id: int, body: CalledAwayBody) -> PositionOut:
    return _run_transition(position_id, body, "called_away")


@router.post("/{position_id}/close-shares", response_model=PositionOut)
def close_shares(position_id: int, body: CloseSharesBody) -> PositionOut:
    return _run_transition(position_id, body, "close_shares")


@router.get("/{position_id}/attribution", response_model=AttributionOut)
def get_attribution(position_id: int) -> AttributionOut:
    with get_session() as session:
        result = attribute(session, position_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"position {position_id} not found")
    return AttributionOut(
        position_id=result.position_id,
        symbol=result.symbol,
        days_in_cycle=result.days_in_cycle,
        total_premium_collected=result.total_premium_collected,
        shares_pnl=result.shares_pnl,
        realized_pnl=result.realized_pnl,
        cost_basis_per_share=result.cost_basis_per_share,
        capital_tied_up=result.capital_tied_up,
        annualized_return=result.annualized_return,
        was_assigned=result.was_assigned,
    )


@router.patch("/{position_id}", response_model=PositionOut)
def patch_position(position_id: int, body: PatchPositionBody) -> PositionOut:
    with get_session() as session:
        position = session.get(Position, position_id)
        if position is None:
            raise HTTPException(status_code=404, detail=f"position {position_id} not found")
        if "notes" in body.model_fields_set:
            position.notes = body.notes
        if "portfolio_id" in body.model_fields_set:
            _require_portfolio_exists(session, body.portfolio_id)
            position.portfolio_id = body.portfolio_id
    return _load(position_id)


def _require_portfolio_exists(session: Session, portfolio_id: int | None) -> None:
    if portfolio_id is None:
        return
    if session.get(Portfolio, portfolio_id) is None:
        raise HTTPException(status_code=422, detail=f"portfolio {portfolio_id} not found")
