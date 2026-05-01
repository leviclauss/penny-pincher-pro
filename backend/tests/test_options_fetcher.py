"""Tests for the option chain fetcher.

Uses a fresh migrated SQLite, a FakeOptionsClient, and asserts:
- fetch passes spot-derived strike bounds and DTE bounds to the client
- snapshots land with all fields normalized
- replace_existing=True wipes stale rows before writing
- symbols with no stored bars are skipped (can't compute strike window)
- upsert is idempotent on re-run
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
from db.models.market import BarDaily, OptionsSnapshot, Ticker
from ingestion.options import fetch_chains
from ingestion.options_client import OptionSnapshotRecord


class FakeOptionsClient:
    def __init__(self, response_by_symbol: dict[str, list[OptionSnapshotRecord]]) -> None:
        self._response = response_by_symbol
        self.calls: list[dict[str, object]] = []

    def get_chain(
        self,
        underlying: str,
        *,
        expiration_gte: date | None = None,
        expiration_lte: date | None = None,
        strike_gte: float | None = None,
        strike_lte: float | None = None,
    ) -> list[OptionSnapshotRecord]:
        self.calls.append(
            {
                "underlying": underlying,
                "expiration_gte": expiration_gte,
                "expiration_lte": expiration_lte,
                "strike_gte": strike_gte,
                "strike_lte": strike_lte,
            }
        )
        return self._response.get(underlying, [])


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "options.db"
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
    s.add(
        BarDaily(
            symbol="AAPL",
            date=date(2024, 5, 1),
            open=170,
            high=172,
            low=169,
            close=171,
            volume=1_000_000,
        )
    )
    s.commit()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _aapl_chain() -> list[OptionSnapshotRecord]:
    return [
        OptionSnapshotRecord(
            symbol="AAPL",
            expiration=date(2024, 5, 17),
            strike=170.0,
            option_type="call",
            bid=2.10,
            ask=2.15,
            last=2.12,
            volume=None,
            open_interest=None,
            delta=0.55,
            gamma=0.04,
            theta=-0.05,
            vega=0.12,
            iv=0.28,
        ),
        OptionSnapshotRecord(
            symbol="AAPL",
            expiration=date(2024, 5, 17),
            strike=170.0,
            option_type="put",
            bid=1.95,
            ask=2.00,
            last=None,
            volume=None,
            open_interest=None,
            delta=-0.45,
            gamma=0.04,
            theta=-0.05,
            vega=0.12,
            iv=0.30,
        ),
    ]


def test_passes_spot_derived_strike_window_and_dte(session: Session) -> None:
    client = FakeOptionsClient({"AAPL": _aapl_chain()})
    fetch_chains(
        session,
        client,
        ["AAPL"],
        max_dte=45,
        strike_pct_window=0.10,
        as_of=date(2024, 5, 2),
    )

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["underlying"] == "AAPL"
    assert call["expiration_gte"] == date(2024, 5, 2)
    assert call["expiration_lte"] == date(2024, 6, 16)
    # Spot 171 ± 10% = [153.9, 188.1]
    assert call["strike_gte"] == 153.9
    assert call["strike_lte"] == 188.1


def test_writes_snapshots_to_db(session: Session) -> None:
    client = FakeOptionsClient({"AAPL": _aapl_chain()})
    summary = fetch_chains(session, client, ["AAPL"], as_of=date(2024, 5, 2))

    assert summary.contracts_written == 2
    assert summary.symbols_with_data == 1

    rows = (
        session.execute(select(OptionsSnapshot).order_by(OptionsSnapshot.option_type))
        .scalars()
        .all()
    )
    assert len(rows) == 2
    call = next(r for r in rows if r.option_type == "call")
    assert call.symbol == "AAPL"
    assert call.strike == 170.0
    assert call.iv == 0.28
    assert call.delta == 0.55
    assert call.snapshot_at is not None


def test_replace_existing_wipes_stale_rows(session: Session) -> None:
    client = FakeOptionsClient({"AAPL": _aapl_chain()})
    fetch_chains(session, client, ["AAPL"], as_of=date(2024, 5, 2))
    initial = session.execute(
        select(func.count()).select_from(OptionsSnapshot).where(OptionsSnapshot.symbol == "AAPL")
    ).scalar_one()
    assert initial == 2

    smaller = [_aapl_chain()[0]]
    client = FakeOptionsClient({"AAPL": smaller})
    fetch_chains(session, client, ["AAPL"], as_of=date(2024, 5, 2))

    after = session.execute(
        select(func.count()).select_from(OptionsSnapshot).where(OptionsSnapshot.symbol == "AAPL")
    ).scalar_one()
    assert after == 1


def test_skips_symbols_with_no_bars(session: Session) -> None:
    client = FakeOptionsClient({"NODATA": _aapl_chain()})
    summary = fetch_chains(session, client, ["NODATA"], as_of=date(2024, 5, 2))

    assert summary.symbols_with_data == 0
    assert client.calls == []


def test_idempotent_when_replace_disabled(session: Session) -> None:
    client = FakeOptionsClient({"AAPL": _aapl_chain()})
    fetch_chains(session, client, ["AAPL"], as_of=date(2024, 5, 2), replace_existing=False)
    first = session.execute(select(func.count()).select_from(OptionsSnapshot)).scalar_one()

    fetch_chains(session, client, ["AAPL"], as_of=date(2024, 5, 2), replace_existing=False)
    second = session.execute(select(func.count()).select_from(OptionsSnapshot)).scalar_one()
    assert first == second


def test_default_to_active_tickers(session: Session) -> None:
    session.add(Ticker(symbol="INACTIVE", is_active=False))
    session.add(
        BarDaily(
            symbol="INACTIVE", date=date(2024, 5, 1), open=10, high=10, low=10, close=10, volume=1
        )
    )
    session.commit()
    client = FakeOptionsClient({"AAPL": _aapl_chain(), "INACTIVE": _aapl_chain()})

    fetch_chains(session, client, as_of=date(2024, 5, 2))

    requested = {c["underlying"] for c in client.calls}
    assert "AAPL" in requested
    assert "INACTIVE" not in requested
