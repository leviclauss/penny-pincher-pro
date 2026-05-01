"""End-to-end pipeline tests.

Exercises run_full and run_incremental over a fresh migrated SQLite + a fake
Alpaca client, asserting that:
- bars and indicators land in the DB
- incremental run only writes new indicator rows
- the CLI parses --full / --incremental flags correctly (smoke)
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
from db.models.market import BarDaily, IndicatorDaily, Ticker
from ingestion.pipeline import run_full, run_incremental
from tests.test_bars_fetcher import FIXTURE_END, FakeAlpacaClient
from tests.test_bars_fetcher import _fake_client_for as build_fake


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "pipeline.db"
    url = f"sqlite:///{db_path}"
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    s.add(Ticker(symbol="AAA", is_active=True))
    s.commit()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def test_run_full_writes_bars_and_indicators(session: Session) -> None:
    client: FakeAlpacaClient = build_fake(["AAA"])
    summary = run_full(session, client, ["AAA"], years=2, end=FIXTURE_END)  # type: ignore[arg-type]

    assert summary.mode == "full"
    assert summary.fetch.bars_written > 0
    assert summary.indicators_written > 0
    assert summary.symbols_processed == 1

    bar_count = session.execute(
        select(func.count()).select_from(BarDaily).where(BarDaily.symbol == "AAA")
    ).scalar_one()
    ind_count = session.execute(
        select(func.count()).select_from(IndicatorDaily).where(IndicatorDaily.symbol == "AAA")
    ).scalar_one()
    assert ind_count == bar_count


def test_run_incremental_only_writes_new_indicator_rows(session: Session) -> None:
    client: FakeAlpacaClient = build_fake(["AAA"])
    run_full(session, client, ["AAA"], years=2, end=date(2024, 6, 30))  # type: ignore[arg-type]

    initial_ind_count = session.execute(
        select(func.count()).select_from(IndicatorDaily).where(IndicatorDaily.symbol == "AAA")
    ).scalar_one()
    assert initial_ind_count > 0

    summary = run_incremental(session, client, ["AAA"], end=FIXTURE_END)  # type: ignore[arg-type]

    final_ind_count = session.execute(
        select(func.count()).select_from(IndicatorDaily).where(IndicatorDaily.symbol == "AAA")
    ).scalar_one()
    delta = final_ind_count - initial_ind_count
    assert summary.indicators_written == delta
    assert delta > 0


def test_cli_help_lists_modes() -> None:
    from click.testing import CliRunner

    from ingestion.pipeline import cli

    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "--full" in result.output
    assert "--incremental" in result.output
