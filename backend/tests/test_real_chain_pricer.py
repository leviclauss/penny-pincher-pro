"""Tests for the RealChainPricer + Pricer Protocol.

Verifies:
- SyntheticPricer matches the underlying free functions (backwards compat).
- RealChainPricer reads close from options_historical.
- RealChainPricer falls back to synthetic when a row is missing.
- select_expiration picks the nearest available expiration.
- select_*_strike snap to actually-available strikes.
- Empty chain → fall back to synthetic for all four methods.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from backtest.pricing import (
    DEFAULT_RISK_FREE_RATE,
    RealChainPricer,
    SyntheticPricer,
    expiration_friday_near,
    price_option,
    select_call_strike,
    select_put_strike,
)
from db.models.market import OptionsHistorical, Ticker


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "real_pricer.db"
    url = f"sqlite:///{db_path}"
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    s.add(Ticker(symbol="AAPL", is_active=True))
    s.commit()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _seed_chain(session: Session) -> None:
    """Three put strikes at one expiration on as_of=2024-05-13.

    Spot ~$170. Strikes at $165 / $170 / $175. Calls at $170 / $175 / $180.
    """
    fetched = datetime.now(UTC)
    rows = []
    for strike, close in [(165.0, 0.95), (170.0, 1.85), (175.0, 4.20)]:
        rows.append(
            OptionsHistorical(
                symbol="AAPL",
                as_of=date(2024, 5, 13),
                expiration=date(2024, 5, 17),
                strike=strike,
                option_type="put",
                close=close,
                fetched_at=fetched,
            )
        )
    for strike, close in [(170.0, 2.10), (175.0, 0.85), (180.0, 0.30)]:
        rows.append(
            OptionsHistorical(
                symbol="AAPL",
                as_of=date(2024, 5, 13),
                expiration=date(2024, 5, 17),
                strike=strike,
                option_type="call",
                close=close,
                fetched_at=fetched,
            )
        )
    # Second expiration to test select_expiration.
    rows.append(
        OptionsHistorical(
            symbol="AAPL",
            as_of=date(2024, 5, 13),
            expiration=date(2024, 6, 21),
            strike=170.0,
            option_type="put",
            close=4.50,
            fetched_at=fetched,
        )
    )
    session.add_all(rows)
    session.commit()


# --------------------------------------------------------------------- #
# SyntheticPricer wraps the underlying free functions
# --------------------------------------------------------------------- #


def test_synthetic_pricer_matches_underlying_price_option() -> None:
    pricer = SyntheticPricer()
    expected = price_option(
        option_type="p",
        spot=100.0,
        strike=95.0,
        as_of=date(2024, 5, 13),
        expiration=date(2024, 6, 21),
        sigma=0.30,
    )
    got = pricer.price_option(
        symbol="AAPL",
        as_of=date(2024, 5, 13),
        option_type="p",
        spot=100.0,
        strike=95.0,
        expiration=date(2024, 6, 21),
        sigma=0.30,
    )
    assert got.mid == pytest.approx(expected.mid)
    assert got.delta == pytest.approx(expected.delta)


def test_synthetic_pricer_select_strike_matches_underlying() -> None:
    pricer = SyntheticPricer()
    spot = 100.0
    expected_put = select_put_strike(spot=spot, target_delta=0.30, sigma=0.30, days_to_expiry=30)
    got_put = pricer.select_put_strike(
        symbol="AAPL",
        as_of=date(2024, 5, 13),
        spot=spot,
        target_delta=0.30,
        expiration=date(2024, 6, 12),
        sigma=0.30,
        days_to_expiry=30,
    )
    assert got_put == expected_put

    expected_call = select_call_strike(
        spot=spot, cost_basis=105.0, target_delta=0.30, sigma=0.30, days_to_expiry=30
    )
    got_call = pricer.select_call_strike(
        symbol="AAPL",
        as_of=date(2024, 5, 13),
        spot=spot,
        cost_basis=105.0,
        target_delta=0.30,
        expiration=date(2024, 6, 12),
        sigma=0.30,
        days_to_expiry=30,
    )
    assert got_call == expected_call


def test_synthetic_pricer_select_expiration_returns_friday() -> None:
    pricer = SyntheticPricer()
    out = pricer.select_expiration(symbol="AAPL", as_of=date(2024, 5, 13), dte_target=30)
    assert out == expiration_friday_near(date(2024, 5, 13), 30)
    assert out.weekday() == 4


# --------------------------------------------------------------------- #
# RealChainPricer reads from options_historical
# --------------------------------------------------------------------- #


def test_real_pricer_returns_db_close_as_mid(session: Session) -> None:
    _seed_chain(session)
    pricer = RealChainPricer(session)

    quote = pricer.price_option(
        symbol="AAPL",
        as_of=date(2024, 5, 13),
        option_type="p",
        spot=170.0,
        strike=170.0,
        expiration=date(2024, 5, 17),
        sigma=0.30,
        risk_free_rate=DEFAULT_RISK_FREE_RATE,
    )
    assert quote.mid == pytest.approx(1.85)
    # Delta is computed via BS — should be roughly -0.5 for ATM put.
    assert -0.6 < quote.delta < -0.4


def test_real_pricer_falls_back_when_row_missing(session: Session) -> None:
    _seed_chain(session)
    pricer = RealChainPricer(session)

    # Strike 999 doesn't exist in the chain → should match synthetic.
    real = pricer.price_option(
        symbol="AAPL",
        as_of=date(2024, 5, 13),
        option_type="p",
        spot=170.0,
        strike=999.0,
        expiration=date(2024, 5, 17),
        sigma=0.30,
    )
    synthetic = SyntheticPricer().price_option(
        symbol="AAPL",
        as_of=date(2024, 5, 13),
        option_type="p",
        spot=170.0,
        strike=999.0,
        expiration=date(2024, 5, 17),
        sigma=0.30,
    )
    assert real.mid == pytest.approx(synthetic.mid)


def test_real_pricer_select_expiration_picks_nearest(session: Session) -> None:
    _seed_chain(session)
    pricer = RealChainPricer(session)

    # Two expirations: 2024-05-17 (4 days out) and 2024-06-21 (39 days out).
    # dte_target=30 → 2024-06-21 wins.
    chosen = pricer.select_expiration(symbol="AAPL", as_of=date(2024, 5, 13), dte_target=30)
    assert chosen == date(2024, 6, 21)

    # dte_target=5 → 2024-05-17 wins.
    chosen = pricer.select_expiration(symbol="AAPL", as_of=date(2024, 5, 13), dte_target=5)
    assert chosen == date(2024, 5, 17)


def test_real_pricer_select_expiration_falls_back_to_synthetic(session: Session) -> None:
    pricer = RealChainPricer(session)
    # Empty chain → fallback to next-Friday-near-target.
    chosen = pricer.select_expiration(symbol="AAPL", as_of=date(2024, 5, 13), dte_target=30)
    assert chosen == expiration_friday_near(date(2024, 5, 13), 30)


def test_real_pricer_select_put_strike_snaps_to_chain(session: Session) -> None:
    _seed_chain(session)
    pricer = RealChainPricer(session)

    # Available put strikes: 165, 170, 175. Target ~30 delta → typically
    # the OTM strike below spot ($165).
    strike = pricer.select_put_strike(
        symbol="AAPL",
        as_of=date(2024, 5, 13),
        spot=170.0,
        target_delta=0.30,
        expiration=date(2024, 5, 17),
        sigma=0.30,
        days_to_expiry=4,
    )
    assert strike in {165.0, 170.0, 175.0}


def test_real_pricer_select_call_strike_floors_at_cost_basis(session: Session) -> None:
    _seed_chain(session)
    pricer = RealChainPricer(session)

    # Available call strikes >= max(spot, cost_basis=178): {180}.
    strike = pricer.select_call_strike(
        symbol="AAPL",
        as_of=date(2024, 5, 13),
        spot=170.0,
        cost_basis=178.0,
        target_delta=0.30,
        expiration=date(2024, 5, 17),
        sigma=0.30,
        days_to_expiry=4,
    )
    assert strike == 180.0


def test_real_pricer_select_call_strike_falls_back_when_no_eligible(session: Session) -> None:
    _seed_chain(session)
    pricer = RealChainPricer(session)

    # Cost basis $200 leaves no eligible call strike (max in chain is $180).
    # Should fall back to the synthetic grid pick.
    strike = pricer.select_call_strike(
        symbol="AAPL",
        as_of=date(2024, 5, 13),
        spot=170.0,
        cost_basis=200.0,
        target_delta=0.30,
        expiration=date(2024, 5, 17),
        sigma=0.30,
        days_to_expiry=4,
    )
    assert strike >= 200.0
