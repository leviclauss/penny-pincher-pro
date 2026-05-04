"""Tests for the flat-file historical option chain backfill.

Uses a fresh migrated SQLite + a FakeFlatFileClient. Verifies:
- spot-relative strike window filters out far-OTM strikes
- bars on days with no underlying close are dropped
- upsert is idempotent on re-run
- symbol with no bars is skipped
- multi-day processing commits per day
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from db.models.market import BarDaily, OptionsHistorical, Ticker
from ingestion.options_history import backfill_history_flatfile
from ingestion.polygon_flatfiles import FlatFileAgg


class FakeFlatFileClient:
    """Injectable fake that returns pre-canned rows keyed by date."""

    def __init__(self, rows_by_day: dict[date, list[FlatFileAgg]]) -> None:
        self._rows = rows_by_day
        self.days_called: list[date] = []

    def iter_day(
        self,
        day: date,
        symbols: frozenset[str],
        *,
        max_dte: int = 60,
    ) -> Iterable[FlatFileAgg]:
        self.days_called.append(day)
        for agg in self._rows.get(day, []):
            if agg.underlying in symbols:
                yield agg


def _agg(
    underlying: str,
    occ: str,
    as_of: date,
    expiration: date,
    strike: float,
    option_type: str = "call",
    close: float = 2.15,
) -> FlatFileAgg:
    return FlatFileAgg(
        occ=occ,
        underlying=underlying,
        expiration=expiration,
        strike=strike,
        option_type=option_type,
        as_of=as_of,
        open=close,
        high=close + 0.05,
        low=close - 0.05,
        close=close,
        volume=100,
    )


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "history_ff.db"
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
    s.add(Ticker(symbol="NODATA", is_active=True))
    for d, close in [
        (date(2024, 5, 13), 170.0),
        (date(2024, 5, 14), 171.0),
        (date(2024, 5, 15), 172.0),
    ]:
        s.add(
            BarDaily(
                symbol="AAPL",
                date=d,
                open=close,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=1_000_000,
            )
        )
    s.commit()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def test_flatfile_writes_rows_within_strike_window(session: Session) -> None:
    rows_by_day = {
        date(2024, 5, 13): [
            _agg("AAPL", "AAPL240517C00170000", date(2024, 5, 13), date(2024, 5, 17), 170.0),
            _agg("AAPL", "AAPL240517C00210000", date(2024, 5, 13), date(2024, 5, 17), 210.0),
        ],
        date(2024, 5, 14): [
            _agg("AAPL", "AAPL240517C00170000", date(2024, 5, 14), date(2024, 5, 17), 170.0),
        ],
        date(2024, 5, 15): [
            _agg("AAPL", "AAPL240517C00170000", date(2024, 5, 15), date(2024, 5, 17), 170.0),
        ],
    }
    client = FakeFlatFileClient(rows_by_day)

    summary = backfill_history_flatfile(
        session,
        client,
        ["AAPL"],
        start=date(2024, 5, 13),
        end=date(2024, 5, 15),
    )
    assert summary.symbols_with_data == 1
    assert summary.rows_written == 3

    rows = session.execute(
        select(OptionsHistorical.as_of, OptionsHistorical.strike).order_by(OptionsHistorical.as_of)
    ).all()
    assert {(r[0], r[1]) for r in rows} == {
        (date(2024, 5, 13), 170.0),
        (date(2024, 5, 14), 170.0),
        (date(2024, 5, 15), 170.0),
    }


def test_flatfile_skips_bars_without_underlying_spot(session: Session) -> None:
    rows_by_day = {
        date(2024, 5, 13): [
            _agg("AAPL", "AAPL240517C00170000", date(2024, 5, 13), date(2024, 5, 17), 170.0),
        ],
        date(2024, 5, 16): [
            _agg("AAPL", "AAPL240517C00170000", date(2024, 5, 16), date(2024, 5, 17), 170.0),
        ],
    }
    client = FakeFlatFileClient(rows_by_day)

    summary = backfill_history_flatfile(
        session, client, ["AAPL"], start=date(2024, 5, 13), end=date(2024, 5, 16)
    )
    assert summary.rows_written == 1
    dates = session.execute(select(OptionsHistorical.as_of)).scalars().all()
    assert dates == [date(2024, 5, 13)]


def test_flatfile_is_idempotent(session: Session) -> None:
    rows_by_day = {
        date(2024, 5, 13): [
            _agg("AAPL", "AAPL240517C00170000", date(2024, 5, 13), date(2024, 5, 17), 170.0),
        ],
    }
    client = FakeFlatFileClient(rows_by_day)

    backfill_history_flatfile(
        session, client, ["AAPL"], start=date(2024, 5, 13), end=date(2024, 5, 13)
    )
    backfill_history_flatfile(
        session, client, ["AAPL"], start=date(2024, 5, 13), end=date(2024, 5, 13)
    )

    count = session.execute(select(func.count()).select_from(OptionsHistorical)).scalar_one()
    assert count == 1


def test_flatfile_skips_symbols_with_no_bars(session: Session) -> None:
    client = FakeFlatFileClient({})
    summary = backfill_history_flatfile(
        session, client, ["NODATA"], start=date(2024, 5, 13), end=date(2024, 5, 15)
    )
    assert summary.symbols_with_data == 0
    assert summary.rows_written == 0


def test_flatfile_default_uses_active_tickers(session: Session) -> None:
    rows_by_day = {
        date(2024, 5, 13): [
            _agg("AAPL", "AAPL240517C00170000", date(2024, 5, 13), date(2024, 5, 17), 170.0),
        ],
    }
    client = FakeFlatFileClient(rows_by_day)

    summary = backfill_history_flatfile(
        session, client, None, start=date(2024, 5, 13), end=date(2024, 5, 13)
    )
    assert summary.rows_written == 1


def test_flatfile_multi_day_multi_symbol(session: Session) -> None:
    session.add(Ticker(symbol="MSFT", is_active=True))
    for d, close in [
        (date(2024, 5, 13), 400.0),
        (date(2024, 5, 14), 401.0),
    ]:
        session.add(
            BarDaily(
                symbol="MSFT",
                date=d,
                open=close,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=500_000,
            )
        )
    session.commit()

    rows_by_day = {
        date(2024, 5, 13): [
            _agg("AAPL", "AAPL240517C00170000", date(2024, 5, 13), date(2024, 5, 17), 170.0),
            _agg("MSFT", "MSFT240517P00400000", date(2024, 5, 13), date(2024, 5, 17), 400.0, "put"),
        ],
        date(2024, 5, 14): [
            _agg("AAPL", "AAPL240517C00170000", date(2024, 5, 14), date(2024, 5, 17), 170.0),
            _agg("MSFT", "MSFT240517P00400000", date(2024, 5, 14), date(2024, 5, 17), 400.0, "put"),
        ],
    }
    client = FakeFlatFileClient(rows_by_day)

    summary = backfill_history_flatfile(
        session, client, ["AAPL", "MSFT"], start=date(2024, 5, 13), end=date(2024, 5, 14)
    )
    assert summary.symbols_with_data == 2
    assert summary.rows_written == 4

    count = session.execute(select(func.count()).select_from(OptionsHistorical)).scalar_one()
    assert count == 4
