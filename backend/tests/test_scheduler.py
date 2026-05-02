"""Tests for the scheduler module.

Mocks the ingestion clients (Alpaca + options) and verifies that the evening
pipeline job records a job_runs row with metrics on success, a holiday-skip
row on closed market days, and a failure row on exceptions. Also smoke-tests
the BackgroundScheduler create/shutdown lifecycle and the manual-trigger
registry resolution.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import cast

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from db.models.market import Ticker
from db.models.system import JobRun
from ingestion.alpaca_client import AlpacaClient
from scheduler.app import create_and_start, get_job_body, register_job, shutdown
from scheduler.context import STATUS_FAILURE, STATUS_SUCCESS
from scheduler.jobs.evening import JOB_NAME as EVENING_JOB_NAME
from scheduler.jobs.evening import run_evening_pipeline
from tests.test_bars_fetcher import FIXTURE_END
from tests.test_bars_fetcher import _fake_client_for as build_fake


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "scheduler.db"
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


def test_evening_pipeline_records_success_with_metrics(session: Session) -> None:
    fake = build_fake(["AAA"])

    run_evening_pipeline(
        session,
        alpaca_client=cast(AlpacaClient, fake),
        options_client=None,
        market_calendar=None,
        as_of=FIXTURE_END,
    )

    row = session.execute(select(JobRun).where(JobRun.job_name == EVENING_JOB_NAME)).scalar_one()
    assert row.status == STATUS_SUCCESS
    assert row.error is None
    assert row.result_json is not None
    assert row.result_json["mode"] == "incremental"
    assert row.result_json["as_of"] == FIXTURE_END.isoformat()
    assert row.result_json["bars"] >= 0
    assert row.result_json["options_contracts"] == 0


def test_evening_pipeline_records_failure(session: Session) -> None:
    class BoomClient:
        def get_daily_bars(self, *args: object, **kwargs: object) -> dict[str, object]:
            raise RuntimeError("simulated alpaca outage")

    with pytest.raises(RuntimeError, match="simulated"):
        run_evening_pipeline(
            session,
            alpaca_client=cast(AlpacaClient, BoomClient()),
            options_client=None,
            market_calendar=None,
            as_of=FIXTURE_END,
        )

    row = session.execute(select(JobRun).where(JobRun.job_name == EVENING_JOB_NAME)).scalar_one()
    assert row.status == STATUS_FAILURE
    assert row.error is not None
    assert "simulated" in row.error


def test_evening_pipeline_skips_on_holiday(session: Session) -> None:
    fake = build_fake(["AAA"])

    run_evening_pipeline(
        session,
        alpaca_client=cast(AlpacaClient, fake),
        options_client=None,
        market_calendar="NYSE",
        as_of=date(2024, 12, 25),
    )

    row = session.execute(select(JobRun).where(JobRun.job_name == EVENING_JOB_NAME)).scalar_one()
    assert row.status == STATUS_SUCCESS
    assert row.result_json is not None
    assert row.result_json["skipped"] == "holiday"


def test_evening_pipeline_runs_on_trading_day(session: Session) -> None:
    fake = build_fake(["AAA"])

    run_evening_pipeline(
        session,
        alpaca_client=cast(AlpacaClient, fake),
        options_client=None,
        market_calendar="NYSE",
        as_of=date(2024, 6, 28),
    )

    row = session.execute(select(JobRun).where(JobRun.job_name == EVENING_JOB_NAME)).scalar_one()
    assert row.status == STATUS_SUCCESS
    assert row.result_json is not None
    assert "skipped" not in row.result_json


def test_scheduler_create_and_shutdown() -> None:
    scheduler = create_and_start()
    try:
        assert scheduler.running is True
        jobs = {job.id for job in scheduler.get_jobs()}
        assert EVENING_JOB_NAME in jobs
    finally:
        shutdown(scheduler)
    assert scheduler.running is False


def test_register_job_round_trip() -> None:
    def body() -> None:
        return None

    register_job("__test_job", lambda: body)
    resolved = get_job_body("__test_job")
    assert resolved is body
    assert get_job_body("does-not-exist") is None
