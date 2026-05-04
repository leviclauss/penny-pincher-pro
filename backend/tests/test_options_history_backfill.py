"""Tests for the historical option chain backfill.

Uses a fresh migrated SQLite + a FakeHistoricalClient. Verifies:
- contracts list + per-contract aggs are written into options_historical
- per-day spot-relative strike window filters out far-OTM strikes
- bars on days with no underlying close are dropped
- upsert is idempotent on re-run
- symbol with no bars is skipped
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
from db.models.market import BarDaily, OptionsHistorical, Ticker
from ingestion.options_history import backfill_history
from ingestion.polygon_client import OptionContractRef, OptionDailyAgg


class FakeHistoricalClient:
    def __init__(
        self,
        contracts_by_symbol: dict[str, list[OptionContractRef]],
        aggs_by_occ: dict[str, list[OptionDailyAgg]],
    ) -> None:
        self._contracts = contracts_by_symbol
        self._aggs = aggs_by_occ
        self.list_calls: list[str] = []
        self.aggs_calls: list[str] = []

    def list_contracts(
        self,
        underlying: str,
        *,
        as_of: date | None = None,
        expiration_gte: date | None = None,
        expiration_lte: date | None = None,
        strike_gte: float | None = None,
        strike_lte: float | None = None,
        include_expired: bool = True,
    ) -> list[OptionContractRef]:
        self.list_calls.append(underlying)
        return self._contracts.get(underlying, [])

    def get_contract_aggs(
        self,
        occ: str,
        *,
        from_date: date,
        to_date: date,
        adjusted: bool = True,
    ) -> list[OptionDailyAgg]:
        self.aggs_calls.append(occ)
        return [b for b in self._aggs.get(occ, []) if from_date <= b.date <= to_date]


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "history.db"
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


def _bar(occ: str, d: date, close: float, vol: int = 100) -> OptionDailyAgg:
    return OptionDailyAgg(
        occ=occ, date=d, open=close, high=close + 0.05, low=close - 0.05, close=close, volume=vol
    )


def test_backfill_writes_rows_within_strike_window(session: Session) -> None:
    in_window = OptionContractRef(
        occ="AAPL240517C00170000",
        underlying="AAPL",
        expiration=date(2024, 5, 17),
        strike=170.0,
        option_type="call",
    )
    out_of_window = OptionContractRef(
        occ="AAPL240517C00210000",
        underlying="AAPL",
        expiration=date(2024, 5, 17),
        strike=210.0,  # > +15% of any spot in the bars
        option_type="call",
    )
    aggs = {
        in_window.occ: [
            _bar(in_window.occ, date(2024, 5, 13), 2.10),
            _bar(in_window.occ, date(2024, 5, 14), 2.05),
            _bar(in_window.occ, date(2024, 5, 15), 2.00),
        ],
        out_of_window.occ: [
            _bar(out_of_window.occ, date(2024, 5, 13), 0.10),
        ],
    }
    client = FakeHistoricalClient(
        contracts_by_symbol={"AAPL": [in_window, out_of_window]}, aggs_by_occ=aggs
    )

    summary = backfill_history(
        session,
        client,
        ["AAPL"],
        start=date(2024, 5, 13),
        end=date(2024, 5, 15),
    )
    assert summary.symbols_with_data == 1
    assert summary.contracts_fetched == 2
    assert summary.rows_written == 3

    rows = session.execute(
        select(OptionsHistorical.as_of, OptionsHistorical.strike).order_by(OptionsHistorical.as_of)
    ).all()
    # Far-OTM contract is filtered out by the spot-relative strike window.
    assert {(r[0], r[1]) for r in rows} == {
        (date(2024, 5, 13), 170.0),
        (date(2024, 5, 14), 170.0),
        (date(2024, 5, 15), 170.0),
    }


def test_backfill_skips_bars_without_underlying_spot(session: Session) -> None:
    contract = OptionContractRef(
        occ="AAPL240517C00170000",
        underlying="AAPL",
        expiration=date(2024, 5, 17),
        strike=170.0,
        option_type="call",
    )
    aggs = {
        contract.occ: [
            _bar(contract.occ, date(2024, 5, 13), 2.10),
            _bar(contract.occ, date(2024, 5, 16), 1.90),  # no AAPL bar this day
        ]
    }
    client = FakeHistoricalClient({"AAPL": [contract]}, aggs)

    summary = backfill_history(
        session, client, ["AAPL"], start=date(2024, 5, 13), end=date(2024, 5, 16)
    )
    assert summary.rows_written == 1
    dates = session.execute(select(OptionsHistorical.as_of)).scalars().all()
    assert dates == [date(2024, 5, 13)]


def test_backfill_is_idempotent(session: Session) -> None:
    contract = OptionContractRef(
        occ="AAPL240517C00170000",
        underlying="AAPL",
        expiration=date(2024, 5, 17),
        strike=170.0,
        option_type="call",
    )
    aggs = {contract.occ: [_bar(contract.occ, date(2024, 5, 13), 2.10)]}
    client = FakeHistoricalClient({"AAPL": [contract]}, aggs)

    backfill_history(session, client, ["AAPL"], start=date(2024, 5, 13), end=date(2024, 5, 13))
    backfill_history(session, client, ["AAPL"], start=date(2024, 5, 13), end=date(2024, 5, 13))

    count = session.execute(select(func.count()).select_from(OptionsHistorical)).scalar_one()
    assert count == 1


def test_backfill_skips_symbols_with_no_bars(session: Session) -> None:
    client = FakeHistoricalClient({"NODATA": []}, {})
    summary = backfill_history(
        session, client, ["NODATA"], start=date(2024, 5, 13), end=date(2024, 5, 15)
    )
    assert summary.symbols_with_data == 0
    assert summary.rows_written == 0
    # NODATA should not have triggered a contracts fetch since spot map was empty.
    assert client.list_calls == []


def test_backfill_default_uses_active_tickers(session: Session) -> None:
    contract = OptionContractRef(
        occ="AAPL240517C00170000",
        underlying="AAPL",
        expiration=date(2024, 5, 17),
        strike=170.0,
        option_type="call",
    )
    aggs = {contract.occ: [_bar(contract.occ, date(2024, 5, 13), 2.10)]}
    client = FakeHistoricalClient({"AAPL": [contract]}, aggs)

    backfill_history(session, client, None, start=date(2024, 5, 13), end=date(2024, 5, 13))
    # Both AAPL and NODATA were enumerated. NODATA short-circuits before the
    # contracts fetch because it has no bars.
    assert "AAPL" in client.list_calls
    assert "NODATA" not in client.list_calls
