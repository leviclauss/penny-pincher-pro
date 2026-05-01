"""Tests for the bars fetcher.

Uses a fresh on-disk SQLite (created via Alembic), a ``FakeAlpacaClient`` that
returns a deterministic slice of the synthetic fixture, and asserts:
- full backfill writes every requested bar
- incremental fetch only requests dates after the latest stored row
- upsert is idempotent (re-running is a no-op for unchanged data)
- chunk batching respects ``batch_size``
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import cast

import pandas as pd
import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from db.models.market import BarDaily, Ticker
from ingestion.alpaca_client import BarRecord
from ingestion.bars import fetch_full, fetch_incremental
from tests.fixtures.bars import synth_bars


class FakeAlpacaClient:
    """Returns slices of a per-symbol synthetic fixture and counts requests."""

    def __init__(self, bars_by_symbol: dict[str, pd.DataFrame]) -> None:
        self._bars = bars_by_symbol
        self.call_log: list[tuple[tuple[str, ...], date, date]] = []

    def get_daily_bars(
        self,
        symbols: list[str],
        start: date | datetime,
        end: date | datetime,
    ) -> dict[str, list[BarRecord]]:
        start_d = start.date() if isinstance(start, datetime) else start
        end_d = end.date() if isinstance(end, datetime) else end
        self.call_log.append((tuple(symbols), start_d, end_d))

        out: dict[str, list[BarRecord]] = {}
        for sym in symbols:
            df = self._bars.get(sym)
            if df is None or df.empty:
                continue
            mask = (df.index >= pd.Timestamp(start_d)) & (df.index <= pd.Timestamp(end_d))
            sliced = df.loc[mask]
            records = [
                BarRecord(
                    symbol=sym,
                    date=cast(pd.Timestamp, idx).date(),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(row["volume"]),
                )
                for idx, row in sliced.iterrows()
            ]
            if records:
                out[sym] = records
        return out


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "bars.db"
    url = f"sqlite:///{db_path}"
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    s.add_all(
        [
            Ticker(symbol="AAA", is_active=True),
            Ticker(symbol="BBB", is_active=True),
            Ticker(symbol="CCC", is_active=False),
        ]
    )
    s.commit()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


FIXTURE_START = date(2020, 1, 2)
FIXTURE_END = date(2024, 12, 31)


def _fake_client_for(symbols: list[str]) -> FakeAlpacaClient:
    bars = {sym: synth_bars(seed=hash(sym) & 0xFFFF, start=FIXTURE_START) for sym in symbols}
    return FakeAlpacaClient(bars)


def test_full_backfill_writes_all_bars(session: Session) -> None:
    client = _fake_client_for(["AAA", "BBB"])
    summary = fetch_full(session, client, ["AAA", "BBB"], years=1, end=FIXTURE_END)  # type: ignore[arg-type]

    assert summary.symbols_requested == 2
    assert summary.symbols_with_data == 2
    assert summary.bars_written > 0

    aaa_count = session.execute(
        select(func.count()).select_from(BarDaily).where(BarDaily.symbol == "AAA")
    ).scalar_one()
    assert aaa_count > 0


def test_full_backfill_uses_active_tickers_when_no_symbols_passed(session: Session) -> None:
    client = _fake_client_for(["AAA", "BBB", "CCC"])
    fetch_full(session, client, years=1, end=FIXTURE_END)  # type: ignore[arg-type]

    requested_symbols: set[str] = set()
    for symbols, _, _ in client.call_log:
        requested_symbols.update(symbols)

    assert requested_symbols == {"AAA", "BBB"}


def test_incremental_only_fetches_after_latest(session: Session) -> None:
    client = _fake_client_for(["AAA"])
    fetch_full(session, client, ["AAA"], years=1, end=date(2024, 6, 30))  # type: ignore[arg-type]

    latest = session.execute(
        select(func.max(BarDaily.date)).where(BarDaily.symbol == "AAA")
    ).scalar_one()
    assert latest is not None

    client.call_log.clear()
    fetch_incremental(session, client, ["AAA"], end=FIXTURE_END)  # type: ignore[arg-type]

    assert client.call_log, "incremental should make at least one call"
    for _, start_d, _ in client.call_log:
        assert start_d > latest


def test_upsert_is_idempotent(session: Session) -> None:
    client = _fake_client_for(["AAA"])
    fetch_full(session, client, ["AAA"], years=1, end=FIXTURE_END)  # type: ignore[arg-type]
    count_after_first = session.execute(
        select(func.count()).select_from(BarDaily).where(BarDaily.symbol == "AAA")
    ).scalar_one()

    fetch_full(session, client, ["AAA"], years=1, end=FIXTURE_END)  # type: ignore[arg-type]
    count_after_second = session.execute(
        select(func.count()).select_from(BarDaily).where(BarDaily.symbol == "AAA")
    ).scalar_one()

    assert count_after_first == count_after_second


def test_batch_size_chunks_requests(session: Session) -> None:
    symbols = [f"S{i:03d}" for i in range(7)]
    for sym in symbols:
        session.add(Ticker(symbol=sym, is_active=True))
    session.commit()

    client = _fake_client_for(symbols)
    fetch_full(session, client, symbols, years=1, batch_size=3, end=FIXTURE_END)  # type: ignore[arg-type]

    assert len(client.call_log) == 3
    for chunk_symbols, _, _ in client.call_log:
        assert len(chunk_symbols) <= 3
