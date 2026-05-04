"""Tests for the IV backfill that replays options_historical → indicators_daily."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from db.models.market import BarDaily, IndicatorDaily, OptionsHistorical, Ticker
from ingestion.iv_backfill import backfill_iv


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "iv_backfill.db"
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


def _seed(session: Session, *, day: date, spot: float, atm_close: float) -> None:
    """Seed bars + ATM put/call rows for one (symbol, day)."""
    session.add(
        BarDaily(
            symbol="AAPL",
            date=day,
            open=spot,
            high=spot + 1,
            low=spot - 1,
            close=spot,
            volume=1_000_000,
        )
    )
    fetched = datetime.now(UTC)
    # Front-month expiration ~10 days out so MIN_DTE_FOR_ATM_IV passes.
    expiration = day + timedelta(days=10)
    for option_type, close in (("call", atm_close), ("put", atm_close)):
        session.add(
            OptionsHistorical(
                symbol="AAPL",
                as_of=day,
                expiration=expiration,
                strike=spot,
                option_type=option_type,
                close=close,
                fetched_at=fetched,
            )
        )
    session.commit()


def test_backfill_writes_iv_atm_to_indicators(session: Session) -> None:
    _seed(session, day=date(2024, 5, 13), spot=170.0, atm_close=2.10)

    summary = backfill_iv(session, ["AAPL"])
    assert summary.symbols_processed == 1
    assert summary.days_with_iv == 1

    iv = session.execute(
        select(IndicatorDaily.iv_atm)
        .where(IndicatorDaily.symbol == "AAPL")
        .where(IndicatorDaily.date == date(2024, 5, 13))
    ).scalar_one()
    assert iv is not None
    assert iv > 0


def test_backfill_skips_days_with_no_underlying_bar(session: Session) -> None:
    # Seed an options row for a date with NO bar — backfill must skip it.
    fetched = datetime.now(UTC)
    session.add(
        OptionsHistorical(
            symbol="AAPL",
            as_of=date(2024, 5, 13),
            expiration=date(2024, 5, 23),
            strike=170.0,
            option_type="call",
            close=2.10,
            fetched_at=fetched,
        )
    )
    session.commit()

    summary = backfill_iv(session, ["AAPL"])
    assert summary.days_with_iv == 0


def test_backfill_respects_start_end_window(session: Session) -> None:
    _seed(session, day=date(2024, 5, 13), spot=170.0, atm_close=2.10)
    _seed(session, day=date(2024, 5, 14), spot=171.0, atm_close=2.20)
    _seed(session, day=date(2024, 5, 15), spot=172.0, atm_close=2.30)

    summary = backfill_iv(session, ["AAPL"], start=date(2024, 5, 14), end=date(2024, 5, 14))
    assert summary.days_with_iv == 1

    dates_with_iv = (
        session.execute(select(IndicatorDaily.date).where(IndicatorDaily.iv_atm.isnot(None)))
        .scalars()
        .all()
    )
    assert dates_with_iv == [date(2024, 5, 14)]


def test_backfill_default_uses_active_tickers(session: Session) -> None:
    _seed(session, day=date(2024, 5, 13), spot=170.0, atm_close=2.10)
    summary = backfill_iv(session, None)
    assert summary.symbols_processed == 1


def test_backfill_is_idempotent(session: Session) -> None:
    _seed(session, day=date(2024, 5, 13), spot=170.0, atm_close=2.10)
    backfill_iv(session, ["AAPL"])
    iv1 = session.execute(
        select(IndicatorDaily.iv_atm).where(IndicatorDaily.symbol == "AAPL")
    ).scalar_one()
    backfill_iv(session, ["AAPL"])
    iv2 = session.execute(
        select(IndicatorDaily.iv_atm).where(IndicatorDaily.symbol == "AAPL")
    ).scalar_one()
    assert iv1 == iv2
