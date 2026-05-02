"""Smoke tests for the system API router.

Covers /api/system/health, /api/system/job-runs, and the manual-trigger
endpoint /api/system/jobs/{name}/run. Scheduler is disabled in the test
client so the lifespan doesn't fire real cron jobs.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from alembic import command
from db.models.system import JobRun
from scheduler.app import register_job


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    db_path = tmp_path / "api.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")

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

    from api.main import app

    with TestClient(app) as c:
        yield c

    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/api/system/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database_url_scheme"] == "sqlite"
    assert payload["bar_count"] == 0
    assert payload["last_bar_date"] is None
    assert "server_time_utc" in payload


def test_job_runs_empty_initially(client: TestClient) -> None:
    response = client.get("/api/system/job-runs")
    assert response.status_code == 200
    assert response.json() == []


def test_job_runs_returns_recent_rows_after_seed(client: TestClient) -> None:
    from core.time import utcnow
    from db import get_session

    with get_session() as session:
        session.add_all(
            [
                JobRun(
                    job_name="evening_pipeline",
                    started_at=utcnow(),
                    ended_at=utcnow(),
                    status="success",
                    result_json={"bars": 100},
                ),
                JobRun(
                    job_name="evening_pipeline",
                    started_at=utcnow(),
                    status="failure",
                    error="boom",
                ),
            ]
        )

    response = client.get("/api/system/job-runs?limit=5")
    payload = response.json()
    assert response.status_code == 200
    assert len(payload) == 2
    assert payload[0]["status"] == "failure"
    assert payload[0]["error"] == "boom"
    assert payload[1]["status"] == "success"
    assert payload[1]["result_json"] == {"bars": 100}


def test_job_runs_filtered_by_name(client: TestClient) -> None:
    from core.time import utcnow
    from db import get_session

    with get_session() as session:
        session.add_all(
            [
                JobRun(job_name="evening_pipeline", started_at=utcnow(), status="success"),
                JobRun(job_name="other", started_at=utcnow(), status="success"),
            ]
        )

    response = client.get("/api/system/job-runs?job_name=other")
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["job_name"] == "other"


def test_trigger_unknown_job_returns_404(client: TestClient) -> None:
    response = client.post("/api/system/jobs/does-not-exist/run")
    assert response.status_code == 404


def test_trigger_runs_registered_job(client: TestClient) -> None:
    from core.time import utcnow
    from db import get_session

    def body() -> None:
        with get_session() as session:
            session.add(JobRun(job_name="manual_demo", started_at=utcnow(), status="success"))

    register_job("manual_demo", lambda: body)

    response = client.post("/api/system/jobs/manual_demo/run")
    assert response.status_code == 202
    payload = response.json()
    assert payload["job_name"] == "manual_demo"
    assert payload["accepted"] is True

    deadline = time.time() + 2.0
    while time.time() < deadline:
        from db import get_session as gs

        with gs() as session:
            row = session.execute(
                select(JobRun).where(JobRun.job_name == "manual_demo")
            ).scalar_one_or_none()
            if row is not None:
                break
        time.sleep(0.05)
    assert row is not None
