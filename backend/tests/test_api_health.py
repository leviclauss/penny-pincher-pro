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

    register_job(
        "manual_demo",
        factory=lambda: body,
        description="manual test",
        cron="0 0 * * *",
        timezone="UTC",
        schedule_human="Daily 00:00 UTC",
    )

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


def test_jobs_endpoint_lists_registered_jobs(client: TestClient) -> None:
    register_job(
        "demo_job",
        factory=lambda: lambda: None,
        description="A demo job",
        cron="30 17 * * mon-fri",
        timezone="America/Los_Angeles",
        schedule_human="Mon-Fri 17:30 America/Los_Angeles",
    )

    response = client.get("/api/system/jobs")
    assert response.status_code == 200
    payload = response.json()
    names = {entry["name"] for entry in payload}
    assert "demo_job" in names
    demo = next(entry for entry in payload if entry["name"] == "demo_job")
    assert demo["description"] == "A demo job"
    assert demo["cron"] == "30 17 * * mon-fri"
    assert demo["schedule"] == "Mon-Fri 17:30 America/Los_Angeles"
    # SCHEDULER_ENABLED=false in fixture → not enabled, no next_run_at.
    assert demo["enabled"] is False
    assert demo["next_run_at"] is None
    assert demo["last_run"] is None


def test_jobs_endpoint_includes_last_run(client: TestClient) -> None:
    from core.time import utcnow
    from db import get_session

    register_job(
        "with_history",
        factory=lambda: lambda: None,
        description="Has run history",
        cron="0 9 * * *",
        timezone="UTC",
        schedule_human="Daily 09:00 UTC",
    )

    with get_session() as session:
        session.add_all(
            [
                JobRun(
                    job_name="with_history",
                    started_at=utcnow(),
                    ended_at=utcnow(),
                    status="success",
                    result_json={"bars": 42},
                ),
                JobRun(
                    job_name="with_history",
                    started_at=utcnow(),
                    ended_at=utcnow(),
                    status="failure",
                    error="kaboom",
                ),
            ]
        )

    response = client.get("/api/system/jobs")
    assert response.status_code == 200
    entry = next(e for e in response.json() if e["name"] == "with_history")
    assert entry["last_run"] is not None
    assert entry["last_run"]["status"] == "failure"
    assert entry["last_run"]["error"] == "kaboom"
