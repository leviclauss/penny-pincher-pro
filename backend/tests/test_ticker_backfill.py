"""Tests for run_ticker_backfill — the helper invoked by POST /api/tickers."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import select

from alembic import command
from db.models.market import IndicatorDaily, Ticker
from db.models.system import JobRun
from tests.test_bars_fetcher import _fake_client_for as build_fake_alpaca


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_path = tmp_path / "backfill.db"
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


def test_run_ticker_backfill_writes_bars_indicators_and_job_run(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from db import get_session
    from ingestion import ticker_backfill

    fake_alpaca = build_fake_alpaca(["AAA"])
    monkeypatch.setattr(ticker_backfill, "build_alpaca_client", lambda: fake_alpaca)
    monkeypatch.setattr(ticker_backfill, "build_options_client", lambda: None)
    monkeypatch.setattr(ticker_backfill, "_build_earnings_client", lambda: None)
    monkeypatch.setattr(ticker_backfill, "_build_metadata_client", lambda: None)

    with get_session() as session:
        session.add(Ticker(symbol="AAA", is_active=True, is_hidden=False))
        session.commit()

    ticker_backfill.run_ticker_backfill("AAA")

    with get_session() as session:
        runs = session.execute(select(JobRun)).scalars().all()
        assert len(runs) == 1
        run = runs[0]
        assert run.job_name == "ticker_backfill"
        assert run.status == "success"
        assert run.result_json is not None
        assert run.result_json["symbol"] == "AAA"
        assert run.result_json["bars"] > 0
        assert run.result_json["indicators"] > 0
        assert run.result_json["options_contracts"] == 0
        assert run.result_json["earnings_rows"] == 0
        assert run.result_json["metadata_updated"] == 0

        ind_count = session.execute(
            select(IndicatorDaily).where(IndicatorDaily.symbol == "AAA")
        ).all()
        assert len(ind_count) > 0


def test_run_ticker_backfill_records_skipped_when_no_alpaca_creds(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from db import get_session
    from ingestion import ticker_backfill

    monkeypatch.setattr(ticker_backfill, "build_alpaca_client", lambda: None)
    monkeypatch.setattr(ticker_backfill, "build_options_client", lambda: None)
    monkeypatch.setattr(ticker_backfill, "_build_earnings_client", lambda: None)
    monkeypatch.setattr(ticker_backfill, "_build_metadata_client", lambda: None)

    with get_session() as session:
        session.add(Ticker(symbol="AAA", is_active=True, is_hidden=False))
        session.commit()

    ticker_backfill.run_ticker_backfill("AAA")

    with get_session() as session:
        run = session.execute(select(JobRun)).scalar_one()
        assert run.status == "success"
        assert run.result_json is not None
        assert run.result_json["skipped"] == "no_alpaca_creds"
