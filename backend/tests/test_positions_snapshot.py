"""Daily-snapshot pass for open wheel positions.

Asserts that mark-to-market P&L, % max profit, delta and DTE land on the
``position_snapshots`` row, that long-shares legs without an open option are
still snapshotted, and that positions without a recent bar are skipped.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import select

from alembic import command
from db import get_session
from db.models.market import BarDaily, OptionsSnapshot, Ticker
from db.models.positions import PositionSnapshot
from positions import state_machine as sm
from positions.snapshot import run_snapshot_pass


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_path = tmp_path / "positions_snap.db"
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


def _seed_underlying(symbol: str, close: float, on: date) -> None:
    with get_session() as session:
        if session.get(Ticker, symbol) is None:
            session.add(
                Ticker(
                    symbol=symbol,
                    is_active=True,
                    is_hidden=False,
                    added_at=datetime(2026, 1, 1, tzinfo=UTC),
                    updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            )
            session.flush()
        session.add(
            BarDaily(
                symbol=symbol,
                date=on,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1_000_000,
            )
        )


def _seed_chain(
    symbol: str,
    expiration: date,
    strike: float,
    option_type: str,
    *,
    bid: float,
    ask: float,
    delta: float,
) -> None:
    with get_session() as session:
        session.add(
            OptionsSnapshot(
                symbol=symbol,
                expiration=expiration,
                strike=strike,
                option_type=option_type,
                bid=bid,
                ask=ask,
                last=(bid + ask) / 2,
                delta=delta,
            )
        )


def _open_short_put(
    symbol: str = "AAPL",
    *,
    expiration: date = date(2026, 6, 19),
    strike: float = 170.0,
    credit: float = 3.00,
    contracts: int = 1,
) -> int:
    with get_session() as session:
        position = sm.open_short_put(
            session,
            sm.OpenShortPutInput(
                symbol=symbol,
                expiration=expiration,
                strike=strike,
                contracts=contracts,
                credit=credit,
                opened_on=date(2026, 5, 1),
            ),
        )
        session.flush()
        return position.id


def test_snapshot_short_put_with_chain(db: None) -> None:
    pid = _open_short_put(credit=3.00)
    _seed_underlying("AAPL", close=175.0, on=date(2026, 5, 5))
    _seed_chain(
        "AAPL",
        expiration=date(2026, 6, 19),
        strike=170.0,
        option_type="put",
        bid=1.40,
        ask=1.60,
        delta=-0.30,
    )

    with get_session() as session:
        summary = run_snapshot_pass(session, as_of=date(2026, 5, 5))

    assert summary.positions_snapshotted == 1
    assert summary.snapshots_written == 1
    with get_session() as session:
        snap = session.execute(
            select(PositionSnapshot).where(PositionSnapshot.position_id == pid)
        ).scalar_one()
        assert snap.underlying_price == 175.0
        assert snap.option_mid == pytest.approx(1.50)
        # (3.00 - 1.50) * 1 * 100 = 150
        assert snap.unrealized_pnl == pytest.approx(150.0)
        assert snap.pct_max_profit == pytest.approx(0.50)
        assert snap.delta == pytest.approx(-0.30)
        assert snap.dte == 45  # 2026-05-05 → 2026-06-19 = 45 days


def test_snapshot_skips_when_no_underlying(db: None) -> None:
    _open_short_put()
    with get_session() as session:
        summary = run_snapshot_pass(session, as_of=date(2026, 5, 5))
    assert summary.snapshots_written == 0
    assert summary.skipped_no_underlying == 1


def test_snapshot_long_shares(db: None) -> None:
    pid = _open_short_put(strike=170.0, credit=2.00)
    with get_session() as session:
        sm.assign_short_put(session, pid, assigned_on=date(2026, 6, 19))

    _seed_underlying("AAPL", close=174.50, on=date(2026, 6, 20))

    with get_session() as session:
        summary = run_snapshot_pass(session, as_of=date(2026, 6, 20))
    assert summary.snapshots_written == 1

    with get_session() as session:
        snap = session.execute(
            select(PositionSnapshot).where(PositionSnapshot.position_id == pid)
        ).scalar_one()
        # No option mark — just shares.
        assert snap.option_mid is None
        # (174.50 - 170) * 100 shares = 450
        assert snap.unrealized_pnl == pytest.approx(450.0)
        assert snap.dte is None


def test_snapshot_covered_call_marks_shares_too(db: None) -> None:
    pid = _open_short_put(strike=170.0, credit=2.00)
    with get_session() as session:
        sm.assign_short_put(session, pid, assigned_on=date(2026, 6, 19))
        sm.open_covered_call(
            session,
            pid,
            sm.OpenCoveredCallInput(
                expiration=date(2026, 7, 17),
                strike=175.0,
                contracts=1,
                credit=1.50,
                opened_on=date(2026, 6, 20),
            ),
        )

    _seed_underlying("AAPL", close=173.0, on=date(2026, 7, 1))
    _seed_chain(
        "AAPL",
        expiration=date(2026, 7, 17),
        strike=175.0,
        option_type="call",
        bid=0.40,
        ask=0.60,
        delta=0.25,
    )

    with get_session() as session:
        run_snapshot_pass(session, as_of=date(2026, 7, 1))

    with get_session() as session:
        snap = session.execute(
            select(PositionSnapshot).where(PositionSnapshot.position_id == pid)
        ).scalar_one()
        # Call P&L: (1.50 - 0.50) * 100 = 100
        # Shares mark: (173 - 170) * 100 = 300 → total 400
        assert snap.unrealized_pnl == pytest.approx(400.0)
        assert snap.delta == pytest.approx(0.25)


def test_snapshot_ignores_closed_positions(db: None) -> None:
    pid = _open_short_put()
    _seed_underlying("AAPL", close=175.0, on=date(2026, 5, 5))
    with get_session() as session:
        sm.expire_short_put(session, pid, expired_on=date(2026, 6, 19))
    with get_session() as session:
        summary = run_snapshot_pass(session, as_of=date(2026, 6, 20))
    assert summary.positions_snapshotted == 0
