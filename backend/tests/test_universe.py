"""Tests for the multi-list universe loader + sync."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import select

from alembic import command
from db import get_session
from db.models.market import Ticker
from ingestion.universe import (
    UNIVERSE_LIST_PATH,
    UNIVERSE_SP400_PATH,
    UNIVERSE_SP600_PATH,
    get_universe_symbols,
    load_all_universe_lists,
    sync_universe_tickers,
)


def test_default_paths_exist() -> None:
    assert UNIVERSE_LIST_PATH.exists()
    assert UNIVERSE_SP400_PATH.exists()
    assert UNIVERSE_SP600_PATH.exists()


def test_load_all_universe_lists_concatenates_and_dedups() -> None:
    entries = load_all_universe_lists()
    symbols = [str(e["symbol"]) for e in entries]
    assert len(symbols) == len(set(symbols)), "loader must dedupe across files"
    # First-occurrence-wins ordering means SP 100 names keep their tier 1/2.
    by_sym = {str(e["symbol"]): e for e in entries}
    if "AAPL" in by_sym:
        tier_raw = by_sym["AAPL"]["tier"]
        assert tier_raw is not None
        assert int(tier_raw) in (1, 2)


def test_load_all_universe_lists_skips_missing(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text(json.dumps([{"symbol": "AAPL", "tier": 1}]))
    missing = tmp_path / "missing.json"
    entries = load_all_universe_lists(paths=(real, missing))
    assert [e["symbol"] for e in entries] == ["AAPL"]


def test_bundled_lists_have_expected_tier_assignments() -> None:
    sp400_entries = json.loads(UNIVERSE_SP400_PATH.read_text())
    sp600_entries = json.loads(UNIVERSE_SP600_PATH.read_text())
    assert all(int(e["tier"]) == 3 for e in sp400_entries)
    assert all(int(e["tier"]) == 4 for e in sp600_entries)
    # Sanity check on size — the expanded universe is meant to be >700 names.
    assert len(sp400_entries) + len(sp600_entries) >= 700


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_path = tmp_path / "universe.db"
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


def test_sync_loads_all_lists_into_db(db: None, tmp_path: Path) -> None:
    a = tmp_path / "a.json"
    a.write_text(json.dumps([{"symbol": "AAPL", "tier": 1, "name": "Apple Inc."}]))
    b = tmp_path / "b.json"
    b.write_text(json.dumps([{"symbol": "MDU", "tier": 3}]))
    c = tmp_path / "c.json"
    c.write_text(json.dumps([{"symbol": "AAOI", "tier": 4}]))

    with get_session() as session:
        summary = sync_universe_tickers(session, universe_paths=(a, b, c))
        assert summary.total == 3
        assert summary.inserted == 3

        symbols = get_universe_symbols(session)
        assert symbols == ["AAOI", "AAPL", "MDU"]

        tiers = {t.symbol: t.tier for t in session.execute(select(Ticker)).scalars()}
        assert tiers == {"AAPL": 1, "MDU": 3, "AAOI": 4}


def test_sync_skips_watchlist_and_reruns_are_idempotent(db: None, tmp_path: Path) -> None:
    a = tmp_path / "a.json"
    a.write_text(json.dumps([{"symbol": "AAPL", "tier": 1}, {"symbol": "MDU", "tier": 3}]))

    # Pre-seed AAPL as a watchlist ticker — the sync must not overwrite it.
    with get_session() as session:
        session.add(
            Ticker(
                symbol="AAPL",
                name="user-curated",
                tier=2,
                is_active=True,
                is_hidden=False,
                ticker_source="watchlist",
            )
        )
        session.commit()

    with get_session() as session:
        summary = sync_universe_tickers(session, universe_paths=(a,))
        assert summary.skipped_watchlist == 1
        assert summary.inserted == 1

        aapl = session.get(Ticker, "AAPL")
        assert aapl is not None
        assert aapl.ticker_source == "watchlist"
        assert aapl.tier == 2  # untouched

    # Second run: everything already there.
    with get_session() as session:
        summary = sync_universe_tickers(session, universe_paths=(a,))
        assert summary.inserted == 0
        assert summary.skipped_already_universe == 1
        assert summary.skipped_watchlist == 1
