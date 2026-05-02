"""Per-cycle performance attribution.

Walks two scenarios end-to-end (premium-only expire vs. assignment +
called-away) and verifies the realized P&L, cost basis, capital tied up,
and annualized-return math.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from db import get_session
from positions import state_machine as sm
from positions.attribution import attribute


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_path = tmp_path / "positions_attr.db"
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


def test_attribute_premium_only_cycle(db: None) -> None:
    with get_session() as session:
        position = sm.open_short_put(
            session,
            sm.OpenShortPutInput(
                symbol="AAPL",
                expiration=date(2026, 6, 19),
                strike=170.0,
                contracts=1,
                credit=2.50,
                opened_on=date(2026, 5, 1),
            ),
        )
        pid = position.id

    with get_session() as session:
        sm.expire_short_put(session, pid, expired_on=date(2026, 6, 19))

    with get_session() as session:
        result = attribute(session, pid)

    assert result is not None
    assert result.realized_pnl == pytest.approx(250.0)
    assert result.total_premium_collected == pytest.approx(250.0)
    assert result.shares_pnl == 0.0
    assert result.was_assigned is False
    assert result.capital_tied_up == pytest.approx(170.0 * 100)  # collateral
    assert result.cost_basis_per_share is None
    # 250 / 17000 * 365 / 49 days ≈ 0.1095
    assert result.annualized_return is not None
    assert result.annualized_return == pytest.approx(250 / 17000 * 365 / 49, rel=1e-3)


def test_attribute_assignment_then_called_away(db: None) -> None:
    with get_session() as session:
        position = sm.open_short_put(
            session,
            sm.OpenShortPutInput(
                symbol="AAPL",
                expiration=date(2026, 6, 19),
                strike=170.0,
                contracts=1,
                credit=2.50,
                opened_on=date(2026, 5, 1),
            ),
        )
        pid = position.id
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
        sm.called_away(session, pid, called_on=date(2026, 7, 17))

    with get_session() as session:
        result = attribute(session, pid)

    assert result is not None
    assert result.was_assigned is True
    # Premium: 250 (put) + 180 (call) = 430
    # Shares: (175 - 170) * 100 = 500
    # Realized: 930
    assert result.total_premium_collected == pytest.approx(430.0)
    assert result.shares_pnl == pytest.approx(500.0)
    assert result.realized_pnl == pytest.approx(930.0)
    # Cost basis = 170 - 2.50 (per-share put credit) = 167.50
    assert result.cost_basis_per_share == pytest.approx(167.50)
    assert result.capital_tied_up == pytest.approx(170 * 100)
    assert result.annualized_return is not None


def test_attribute_unknown_position(db: None) -> None:
    with get_session() as session:
        assert attribute(session, 9999) is None
