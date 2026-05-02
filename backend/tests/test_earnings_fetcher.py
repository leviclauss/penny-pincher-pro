"""Tests for the earnings fetcher.

Uses a fresh migrated SQLite + a FakeFinnhubClient. Verifies that the calendar
is filtered to active tickers, upserts are idempotent, and time_of_day
revisions overwrite on re-fetch.
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
from db.models.market import Earnings, Ticker
from ingestion.earnings import fetch_earnings
from ingestion.finnhub_client import EarningsRecord


class FakeFinnhubClient:
    def __init__(self, records: list[EarningsRecord]) -> None:
        self._records = records
        self.calls: list[dict[str, object]] = []

    def get_earnings_calendar(
        self,
        *,
        from_date: date,
        to_date: date,
        symbol: str | None = None,
    ) -> list[EarningsRecord]:
        self.calls.append({"from_date": from_date, "to_date": to_date, "symbol": symbol})
        if symbol is None:
            return list(self._records)
        return [r for r in self._records if r.symbol == symbol]


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "earnings.db"
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
    s.add(Ticker(symbol="MSFT", is_active=True))
    s.add(Ticker(symbol="INACTIVE", is_active=False))
    s.commit()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _records() -> list[EarningsRecord]:
    return [
        EarningsRecord(symbol="AAPL", earnings_date=date(2026, 5, 5), time_of_day="AMC"),
        EarningsRecord(symbol="MSFT", earnings_date=date(2026, 5, 6), time_of_day="BMO"),
        EarningsRecord(symbol="INACTIVE", earnings_date=date(2026, 5, 7), time_of_day=None),
        EarningsRecord(symbol="OFFLIST", earnings_date=date(2026, 5, 8), time_of_day=None),
    ]


def test_filters_to_active_tickers(session: Session) -> None:
    client = FakeFinnhubClient(_records())
    summary = fetch_earnings(session, client, lookahead_days=30, as_of=date(2026, 5, 1))

    assert summary.symbols_in_window == 2
    assert summary.rows_written == 2
    assert summary.window_from == date(2026, 5, 1)
    assert summary.window_to == date(2026, 5, 31)

    rows = session.execute(select(Earnings).order_by(Earnings.symbol)).scalars().all()
    symbols = [r.symbol for r in rows]
    assert symbols == ["AAPL", "MSFT"]


def test_passes_window_to_client(session: Session) -> None:
    client = FakeFinnhubClient([])
    fetch_earnings(session, client, lookahead_days=45, as_of=date(2026, 5, 1))
    assert client.calls == [
        {"from_date": date(2026, 5, 1), "to_date": date(2026, 6, 15), "symbol": "AAPL"},
        {"from_date": date(2026, 5, 1), "to_date": date(2026, 6, 15), "symbol": "MSFT"},
    ]


def test_upsert_overwrites_time_of_day(session: Session) -> None:
    client = FakeFinnhubClient(
        [EarningsRecord(symbol="AAPL", earnings_date=date(2026, 5, 5), time_of_day="AMC")]
    )
    fetch_earnings(session, client, as_of=date(2026, 5, 1))

    revised = FakeFinnhubClient(
        [EarningsRecord(symbol="AAPL", earnings_date=date(2026, 5, 5), time_of_day="BMO")]
    )
    fetch_earnings(session, revised, as_of=date(2026, 5, 1))

    count = session.execute(select(func.count()).select_from(Earnings)).scalar_one()
    assert count == 1
    tod = session.execute(
        select(Earnings.time_of_day).where(Earnings.symbol == "AAPL")
    ).scalar_one()
    assert tod == "BMO"


def test_explicit_symbols_override_active_filter(session: Session) -> None:
    client = FakeFinnhubClient(_records())
    fetch_earnings(session, client, symbols=["INACTIVE"], lookahead_days=30, as_of=date(2026, 5, 1))

    rows = session.execute(select(Earnings).order_by(Earnings.symbol)).scalars().all()
    assert [r.symbol for r in rows] == ["INACTIVE"]
