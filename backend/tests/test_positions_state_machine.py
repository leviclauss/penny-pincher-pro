"""State-machine transitions for the wheel lifecycle.

Walks every legal path through the graph (open → close, expire, assign →
covered call → close/expire/called away) plus the manual share-sale exit.
Also asserts the guard rails for invalid transitions.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import select

from alembic import command
from db import get_session
from db.models.positions import Position, PositionLeg
from positions import state_machine as sm


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_path = tmp_path / "positions_sm.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

    from core.config import get_settings
    from db import session as db_session

    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()

    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    yield

    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()


def _open_put(symbol: str = "AAPL", contracts: int = 1, credit: float = 2.50) -> int:
    with get_session() as session:
        position = sm.open_short_put(
            session,
            sm.OpenShortPutInput(
                symbol=symbol,
                expiration=date(2026, 6, 19),
                strike=170.0,
                contracts=contracts,
                credit=credit,
                opened_on=date(2026, 5, 1),
            ),
        )
        session.flush()
        return position.id


def test_open_short_put_creates_position_and_leg(db: None) -> None:
    pid = _open_put()

    with get_session() as session:
        position = session.get(Position, pid)
        assert position is not None
        assert position.symbol == "AAPL"
        assert position.state == sm.STATE_SHORT_PUT
        assert position.cycle_id == pid
        assert position.opened_at is not None
        assert position.closed_at is None

        legs = (
            session.execute(select(PositionLeg).where(PositionLeg.position_id == pid))
            .scalars()
            .all()
        )
        assert len(legs) == 1
        leg = legs[0]
        assert leg.leg_type == sm.LEG_SHORT_PUT
        assert leg.outcome == sm.OUTCOME_OPEN
        assert leg.entry_price == 2.50
        assert leg.contracts == 1


def test_close_short_put_realizes_pnl(db: None) -> None:
    pid = _open_put(credit=3.00)
    with get_session() as session:
        sm.close_short_put(session, pid, debit=1.20, closed_on=date(2026, 5, 15), fees=1.30)

    with get_session() as session:
        position = session.get(Position, pid)
        assert position is not None
        assert position.state == sm.STATE_CLOSED
        leg = session.execute(
            select(PositionLeg).where(PositionLeg.position_id == pid)
        ).scalar_one()
        # (3.00 - 1.20) * 1 contract * 100 = 180; 180 - 1.30 fees = 178.70
        assert leg.outcome == sm.OUTCOME_CLOSED
        assert leg.realized_pnl == pytest.approx(178.70)
        assert leg.exit_price == 1.20


def test_expire_short_put_realizes_full_premium(db: None) -> None:
    pid = _open_put(credit=2.00, contracts=2)
    with get_session() as session:
        sm.expire_short_put(session, pid, expired_on=date(2026, 6, 19))

    with get_session() as session:
        position = session.get(Position, pid)
        assert position is not None
        assert position.state == sm.STATE_CLOSED
        leg = session.execute(
            select(PositionLeg).where(PositionLeg.position_id == pid)
        ).scalar_one()
        assert leg.outcome == sm.OUTCOME_EXPIRED
        assert leg.realized_pnl == pytest.approx(2.00 * 2 * 100)


def test_assign_creates_shares_leg_and_keeps_premium(db: None) -> None:
    pid = _open_put(credit=2.50, contracts=1)
    with get_session() as session:
        sm.assign_short_put(session, pid, assigned_on=date(2026, 6, 19))

    with get_session() as session:
        position = session.get(Position, pid)
        assert position is not None
        assert position.state == sm.STATE_LONG_SHARES
        legs = (
            session.execute(
                select(PositionLeg).where(PositionLeg.position_id == pid).order_by(PositionLeg.id)
            )
            .scalars()
            .all()
        )
        assert len(legs) == 2
        put, shares = legs
        assert put.outcome == sm.OUTCOME_ASSIGNED
        assert put.realized_pnl == pytest.approx(250.0)  # full premium kept
        assert shares.leg_type == sm.LEG_SHARES
        assert shares.shares == 100
        assert shares.entry_price == 170.0
        assert shares.outcome == sm.OUTCOME_OPEN


def test_full_cycle_assignment_then_called_away(db: None) -> None:
    pid = _open_put(credit=2.50)
    with get_session() as session:
        sm.assign_short_put(session, pid, assigned_on=date(2026, 6, 19))
        sm.open_covered_call(
            session,
            pid,
            sm.OpenCoveredCallInput(
                expiration=date(2026, 7, 17),
                strike=175.0,
                contracts=1,
                credit=1.80,
                opened_on=date(2026, 6, 20),
            ),
        )

    with get_session() as session:
        cc_position = session.get(Position, pid)
        assert cc_position is not None
        assert cc_position.state == sm.STATE_COVERED_CALL

    with get_session() as session:
        sm.called_away(session, pid, called_on=date(2026, 7, 17))

    with get_session() as session:
        position = session.get(Position, pid)
        assert position is not None
        assert position.state == sm.STATE_CLOSED
        legs = (
            session.execute(
                select(PositionLeg).where(PositionLeg.position_id == pid).order_by(PositionLeg.id)
            )
            .scalars()
            .all()
        )
        put, shares, call = legs
        assert call.outcome == sm.OUTCOME_CALLED_AWAY
        assert call.realized_pnl == pytest.approx(180.0)
        assert shares.outcome == sm.OUTCOME_CALLED_AWAY
        # shares: (175 - 170) * 100 = 500
        assert shares.realized_pnl == pytest.approx(500.0)
        assert put.outcome == sm.OUTCOME_ASSIGNED


def test_close_covered_call_returns_to_long_shares(db: None) -> None:
    pid = _open_put(credit=2.50)
    with get_session() as session:
        sm.assign_short_put(session, pid, assigned_on=date(2026, 6, 19))
        sm.open_covered_call(
            session,
            pid,
            sm.OpenCoveredCallInput(
                expiration=date(2026, 7, 17),
                strike=175.0,
                contracts=1,
                credit=1.80,
                opened_on=date(2026, 6, 20),
            ),
        )
        sm.close_covered_call(session, pid, debit=0.50, closed_on=date(2026, 7, 1))

    with get_session() as session:
        position = session.get(Position, pid)
        assert position is not None
        assert position.state == sm.STATE_LONG_SHARES
        call = session.execute(
            select(PositionLeg).where(
                PositionLeg.position_id == pid,
                PositionLeg.leg_type == sm.LEG_COVERED_CALL,
            )
        ).scalar_one()
        assert call.outcome == sm.OUTCOME_CLOSED
        assert call.realized_pnl == pytest.approx(130.0)


def test_expire_covered_call_returns_to_long_shares(db: None) -> None:
    pid = _open_put(credit=2.50)
    with get_session() as session:
        sm.assign_short_put(session, pid, assigned_on=date(2026, 6, 19))
        sm.open_covered_call(
            session,
            pid,
            sm.OpenCoveredCallInput(
                expiration=date(2026, 7, 17),
                strike=175.0,
                contracts=1,
                credit=1.80,
                opened_on=date(2026, 6, 20),
            ),
        )
        sm.expire_covered_call(session, pid, expired_on=date(2026, 7, 17))

    with get_session() as session:
        position = session.get(Position, pid)
        assert position is not None
        assert position.state == sm.STATE_LONG_SHARES
        call = session.execute(
            select(PositionLeg).where(
                PositionLeg.position_id == pid,
                PositionLeg.leg_type == sm.LEG_COVERED_CALL,
            )
        ).scalar_one()
        assert call.outcome == sm.OUTCOME_EXPIRED
        assert call.realized_pnl == pytest.approx(180.0)


def test_close_shares_manual(db: None) -> None:
    pid = _open_put(credit=2.50)
    with get_session() as session:
        sm.assign_short_put(session, pid, assigned_on=date(2026, 6, 19))
        sm.close_shares_manual(session, pid, sale_price=172.0, closed_on=date(2026, 7, 1))

    with get_session() as session:
        position = session.get(Position, pid)
        assert position is not None
        assert position.state == sm.STATE_CLOSED
        shares = session.execute(
            select(PositionLeg).where(
                PositionLeg.position_id == pid, PositionLeg.leg_type == sm.LEG_SHARES
            )
        ).scalar_one()
        assert shares.outcome == sm.OUTCOME_CLOSED
        assert shares.realized_pnl == pytest.approx(200.0)


def test_invalid_transition_close_put_when_long_shares(db: None) -> None:
    pid = _open_put()
    with get_session() as session:
        sm.assign_short_put(session, pid, assigned_on=date(2026, 6, 19))
    with (
        get_session() as session,
        pytest.raises(sm.InvalidTransitionError),
    ):
        sm.close_short_put(session, pid, debit=1.0, closed_on=date(2026, 7, 1))


def test_open_call_requires_long_shares(db: None) -> None:
    pid = _open_put()
    with (
        get_session() as session,
        pytest.raises(sm.InvalidTransitionError),
    ):
        sm.open_covered_call(
            session,
            pid,
            sm.OpenCoveredCallInput(
                expiration=date(2026, 7, 17),
                strike=175.0,
                contracts=1,
                credit=1.80,
                opened_on=date(2026, 6, 20),
            ),
        )


def test_open_short_put_validates_inputs(db: None) -> None:
    with (
        get_session() as session,
        pytest.raises(sm.InvalidLegError, match="contracts"),
    ):
        sm.open_short_put(
            session,
            sm.OpenShortPutInput(
                symbol="AAPL",
                expiration=date(2026, 6, 19),
                strike=170.0,
                contracts=0,
                credit=2.50,
                opened_on=date(2026, 5, 1),
            ),
        )


def test_position_not_found(db: None) -> None:
    with (
        get_session() as session,
        pytest.raises(sm.PositionError, match="not found"),
    ):
        sm.close_short_put(session, 9999, debit=1.0, closed_on=date(2026, 5, 15))


def _open_long_shares(
    symbol: str = "MSFT",
    shares: int = 200,
    cost_basis: float = 410.50,
    acquisition_source: str = sm.ACQUISITION_OPEN_MARKET,
    fees: float = 0.0,
    notes: str | None = None,
) -> int:
    with get_session() as session:
        position = sm.open_long_shares(
            session,
            sm.OpenLongSharesInput(
                symbol=symbol,
                shares=shares,
                cost_basis=cost_basis,
                opened_on=date(2026, 4, 15),
                acquisition_source=acquisition_source,
                fees=fees,
                notes=notes,
            ),
        )
        session.flush()
        return position.id


def test_open_long_shares_creates_position_and_leg(db: None) -> None:
    pid = _open_long_shares(symbol="msft", fees=1.25, notes="bought during dip")

    with get_session() as session:
        position = session.get(Position, pid)
        assert position is not None
        assert position.symbol == "MSFT"
        assert position.state == sm.STATE_LONG_SHARES
        assert position.acquisition_source == sm.ACQUISITION_OPEN_MARKET
        assert position.notes == "bought during dip"
        assert position.cycle_id == pid

        leg = session.execute(
            select(PositionLeg).where(PositionLeg.position_id == pid)
        ).scalar_one()
        assert leg.leg_type == sm.LEG_SHARES
        assert leg.shares == 200
        assert leg.entry_price == pytest.approx(410.50)
        assert leg.entry_date == date(2026, 4, 15)
        assert leg.outcome == sm.OUTCOME_OPEN
        assert leg.fees == pytest.approx(1.25)


def test_open_long_shares_records_assignment_source(db: None) -> None:
    pid = _open_long_shares(
        symbol="NVDA",
        shares=100,
        cost_basis=120.0,
        acquisition_source=sm.ACQUISITION_ASSIGNMENT,
    )

    with get_session() as session:
        position = session.get(Position, pid)
        assert position is not None
        assert position.acquisition_source == sm.ACQUISITION_ASSIGNMENT


def test_open_long_shares_validates_inputs(db: None) -> None:
    with (
        get_session() as session,
        pytest.raises(sm.InvalidLegError, match="shares"),
    ):
        sm.open_long_shares(
            session,
            sm.OpenLongSharesInput(
                symbol="AAPL",
                shares=0,
                cost_basis=170.0,
                opened_on=date(2026, 5, 1),
                acquisition_source=sm.ACQUISITION_OPEN_MARKET,
            ),
        )

    with (
        get_session() as session,
        pytest.raises(sm.InvalidLegError, match="cost_basis"),
    ):
        sm.open_long_shares(
            session,
            sm.OpenLongSharesInput(
                symbol="AAPL",
                shares=100,
                cost_basis=0.0,
                opened_on=date(2026, 5, 1),
                acquisition_source=sm.ACQUISITION_OPEN_MARKET,
            ),
        )

    with (
        get_session() as session,
        pytest.raises(sm.InvalidLegError, match="acquisition_source"),
    ):
        sm.open_long_shares(
            session,
            sm.OpenLongSharesInput(
                symbol="AAPL",
                shares=100,
                cost_basis=170.0,
                opened_on=date(2026, 5, 1),
                acquisition_source="inheritance",
            ),
        )


def test_open_covered_call_fresh_creates_two_legs(db: None) -> None:
    with get_session() as session:
        new_position = sm.open_covered_call_fresh(
            session,
            sm.OpenCoveredCallFreshInput(
                symbol="aapl",
                shares=200,
                cost_basis=170.0,
                opened_on=date(2026, 5, 1),
                acquisition_source=sm.ACQUISITION_OPEN_MARKET,
                expiration=date(2026, 6, 19),
                strike=180.0,
                contracts=2,
                credit=2.40,
                fees=0.65,
            ),
        )
        session.flush()
        pid = new_position.id

    with get_session() as session:
        position = session.get(Position, pid)
        assert position is not None
        assert position.symbol == "AAPL"
        assert position.state == sm.STATE_COVERED_CALL
        assert position.acquisition_source == sm.ACQUISITION_OPEN_MARKET

        legs = (
            session.execute(
                select(PositionLeg).where(PositionLeg.position_id == pid).order_by(PositionLeg.id)
            )
            .scalars()
            .all()
        )
        assert len(legs) == 2
        shares, call = legs
        assert shares.leg_type == sm.LEG_SHARES
        assert shares.shares == 200
        assert shares.entry_price == pytest.approx(170.0)
        assert shares.fees == pytest.approx(0.65)
        assert call.leg_type == sm.LEG_COVERED_CALL
        assert call.contracts == 2
        assert call.strike == pytest.approx(180.0)
        assert call.entry_price == pytest.approx(2.40)
        assert call.outcome == sm.OUTCOME_OPEN


def test_open_covered_call_fresh_rejects_undercovered_call(db: None) -> None:
    with (
        get_session() as session,
        pytest.raises(sm.InvalidLegError, match="not enough shares"),
    ):
        sm.open_covered_call_fresh(
            session,
            sm.OpenCoveredCallFreshInput(
                symbol="AAPL",
                shares=100,
                cost_basis=170.0,
                opened_on=date(2026, 5, 1),
                acquisition_source=sm.ACQUISITION_OPEN_MARKET,
                expiration=date(2026, 6, 19),
                strike=180.0,
                contracts=2,
                credit=2.40,
            ),
        )


def test_open_covered_call_fresh_rejects_bad_dates(db: None) -> None:
    with (
        get_session() as session,
        pytest.raises(sm.InvalidLegError, match="expiration"),
    ):
        sm.open_covered_call_fresh(
            session,
            sm.OpenCoveredCallFreshInput(
                symbol="AAPL",
                shares=100,
                cost_basis=170.0,
                opened_on=date(2026, 6, 20),
                acquisition_source=sm.ACQUISITION_OPEN_MARKET,
                expiration=date(2026, 6, 19),
                strike=180.0,
                contracts=1,
                credit=2.40,
            ),
        )


def test_open_call_too_many_contracts(db: None) -> None:
    pid = _open_put(contracts=1)
    with get_session() as session:
        sm.assign_short_put(session, pid, assigned_on=date(2026, 6, 19))
    with (
        get_session() as session,
        pytest.raises(sm.InvalidLegError, match="not enough shares"),
    ):
        sm.open_covered_call(
            session,
            pid,
            sm.OpenCoveredCallInput(
                expiration=date(2026, 7, 17),
                strike=175.0,
                contracts=2,
                credit=1.80,
                opened_on=date(2026, 6, 20),
            ),
        )
