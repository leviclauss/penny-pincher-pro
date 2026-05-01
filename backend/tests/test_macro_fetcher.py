"""Tests for the macro fetcher.

Uses a fresh migrated SQLite + a FakeYahooClient. Verifies that VIX/VIX9D
get composed with locally-stored SPY data, term structure / regime are
derived correctly, NULL gaps are tolerated, and re-running upserts.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from db.models.market import BarDaily, IndicatorDaily, MacroDaily, Ticker
from ingestion.macro import VIX9D_SYMBOL, VIX_SYMBOL, fetch_macro
from ingestion.yahoo_client import IndexBarRecord


class FakeYahooClient:
    def __init__(self, by_symbol: dict[str, list[IndexBarRecord]]) -> None:
        self._by_symbol = by_symbol
        self.calls: list[dict[str, object]] = []

    def get_index_history(
        self,
        symbol: str,
        *,
        days_back: int,
        as_of: date | None = None,
    ) -> list[IndexBarRecord]:
        self.calls.append({"symbol": symbol, "days_back": days_back, "as_of": as_of})
        return list(self._by_symbol.get(symbol, []))


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "macro.db"
    url = f"sqlite:///{db_path}"
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    s.add(Ticker(symbol="SPY", is_active=True))
    s.add_all(
        [
            BarDaily(
                symbol="SPY",
                date=d,
                open=p,
                high=p + 1,
                low=p - 1,
                close=p,
                volume=1_000_000,
            )
            for d, p in [
                (date(2026, 4, 29), 700.0),
                (date(2026, 4, 30), 705.0),
                (date(2026, 5, 1), 710.0),
            ]
        ]
    )
    s.add_all(
        [
            IndicatorDaily(symbol="SPY", date=d, ema_200=e)
            for d, e in [
                (date(2026, 4, 29), 690.0),
                (date(2026, 4, 30), 691.0),
                (date(2026, 5, 1), 715.0),
            ]
        ]
    )
    s.commit()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _vix() -> list[IndexBarRecord]:
    return [
        IndexBarRecord(symbol=VIX_SYMBOL, date=date(2026, 4, 29), close=18.0),
        IndexBarRecord(symbol=VIX_SYMBOL, date=date(2026, 4, 30), close=17.5),
        IndexBarRecord(symbol=VIX_SYMBOL, date=date(2026, 5, 1), close=20.0),
    ]


def _vix9d() -> list[IndexBarRecord]:
    return [
        IndexBarRecord(symbol=VIX9D_SYMBOL, date=date(2026, 4, 29), close=16.0),
        IndexBarRecord(symbol=VIX9D_SYMBOL, date=date(2026, 4, 30), close=18.0),
    ]


def test_writes_composed_macro_rows(session: Session) -> None:
    client = FakeYahooClient({VIX_SYMBOL: _vix(), VIX9D_SYMBOL: _vix9d()})
    summary = fetch_macro(session, client, lookback_days=30, as_of=date(2026, 5, 1))

    assert summary.rows_written == 3
    assert summary.earliest == date(2026, 4, 29)
    assert summary.latest == date(2026, 5, 1)

    rows = {
        r.date: r
        for r in session.execute(select(MacroDaily).order_by(MacroDaily.date)).scalars().all()
    }
    backwardation = rows[date(2026, 4, 29)]
    assert backwardation.vix_close == pytest.approx(18.0)
    assert backwardation.vix_9d == pytest.approx(16.0)
    assert backwardation.vix_term_structure == pytest.approx(16.0 / 18.0)
    assert backwardation.spy_close == pytest.approx(700.0)
    assert backwardation.spy_above_200ema is True

    contango = rows[date(2026, 4, 30)]
    assert contango.vix_term_structure == pytest.approx(18.0 / 17.5)

    risk_off = rows[date(2026, 5, 1)]
    assert risk_off.vix_9d is None
    assert risk_off.vix_term_structure is None
    assert risk_off.spy_above_200ema is False


def test_upsert_overwrites_on_revision(session: Session) -> None:
    client = FakeYahooClient({VIX_SYMBOL: _vix(), VIX9D_SYMBOL: _vix9d()})
    fetch_macro(session, client, lookback_days=30, as_of=date(2026, 5, 1))

    revised_vix = [
        IndexBarRecord(symbol=VIX_SYMBOL, date=date(2026, 4, 29), close=22.0),
    ]
    fetch_macro(
        session,
        FakeYahooClient({VIX_SYMBOL: revised_vix, VIX9D_SYMBOL: []}),
        lookback_days=30,
        as_of=date(2026, 5, 1),
    )

    count = session.execute(select(func.count()).select_from(MacroDaily)).scalar_one()
    assert count == 3
    refreshed = session.execute(
        select(MacroDaily.vix_close).where(MacroDaily.date == date(2026, 4, 29))
    ).scalar_one()
    assert refreshed == pytest.approx(22.0)


def test_handles_empty_response(session: Session) -> None:
    client = FakeYahooClient({})
    summary = fetch_macro(session, client, lookback_days=30, as_of=date(2026, 5, 1))
    assert summary.rows_written == 3
    rows = session.execute(select(MacroDaily)).scalars().all()
    assert all(r.vix_close is None for r in rows)
