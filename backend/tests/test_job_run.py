"""Tests for the ``job_run`` context manager.

Verifies a job_runs row is always written (success and failure paths),
result metrics merge correctly, exceptions propagate, and timestamps
populate sanely.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from db.models.system import JobRun
from scheduler.context import STATUS_FAILURE, STATUS_SUCCESS, job_run


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "jobruns.db"
    url = f"sqlite:///{db_path}"
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def test_success_writes_row_with_metrics(session: Session) -> None:
    with job_run(session, "demo") as ctx:
        ctx.set_result(symbols=10, bars=2520)
        ctx.set_result(extra="ok")

    row = session.execute(select(JobRun).where(JobRun.job_name == "demo")).scalar_one()
    assert row.status == STATUS_SUCCESS
    assert row.error is None
    assert row.started_at is not None
    assert row.ended_at is not None
    assert row.ended_at >= row.started_at
    assert row.result_json == {"symbols": 10, "bars": 2520, "extra": "ok"}


def test_failure_records_exception_and_reraises(session: Session) -> None:
    with pytest.raises(RuntimeError, match="boom"), job_run(session, "demo"):
        raise RuntimeError("boom")

    row = session.execute(select(JobRun).where(JobRun.job_name == "demo")).scalar_one()
    assert row.status == STATUS_FAILURE
    assert row.error is not None
    assert "RuntimeError: boom" in row.error
    assert row.ended_at is not None


def test_handle_exposes_id_during_block(session: Session) -> None:
    captured: dict[str, int] = {}
    with job_run(session, "demo") as ctx:
        captured["id"] = ctx.id

    row = session.execute(select(JobRun).where(JobRun.job_name == "demo")).scalar_one()
    assert captured["id"] == row.id
