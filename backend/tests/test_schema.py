"""Schema smoke test: applies migrations to a temp SQLite DB and asserts every
expected table exists. Acts as a guardrail against partner/track drift."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command

EXPECTED_TABLES = {
    "alert_preferences",
    "alerts",
    "backtest_equity",
    "backtest_runs",
    "backtest_trades",
    "bars_daily",
    "earnings",
    "filter_configs",
    "indicators_daily",
    "job_runs",
    "macro_daily",
    "options_snapshot",
    "position_legs",
    "position_snapshots",
    "positions",
    "screener_results",
    "tickers",
}


@pytest.fixture
def migrated_db_url(tmp_path: Path) -> Iterator[str]:
    db_path = tmp_path / "schema_test.db"
    url = f"sqlite:///{db_path}"
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    yield url


def test_all_tables_present(migrated_db_url: str) -> None:
    engine = create_engine(migrated_db_url)
    tables = set(inspect(engine).get_table_names())
    missing = EXPECTED_TABLES - tables
    assert not missing, f"missing tables after migrate: {missing}"
