"""System router — health, job-run history, and manual job triggers.

- ``GET /api/system/health`` is what the frontend's status panel and external
  uptime monitors poll.
- ``GET /api/system/job-runs`` exposes the recent ``job_runs`` rows so the UI
  can show "did the evening pipeline run last night, and what did it write?"
- ``POST /api/system/jobs/{name}/run`` lets you re-fire any registered job
  on demand (used both for "run ingestion now" buttons and for debugging).
  The job runs in a background thread; the response returns immediately
  with the ``job_run.id`` so callers can poll for completion.
"""

from __future__ import annotations

import threading
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from core.config import get_settings
from core.time import utcnow
from db import get_session
from db.models.market import BarDaily
from db.models.system import JobRun
from scheduler.app import get_job_body

router = APIRouter(prefix="/api/system", tags=["system"])


class HealthStatus(BaseModel):
    status: str
    app_env: str
    server_time_utc: str
    database_url_scheme: str
    last_bar_date: date | None
    bar_count: int


class JobRunOut(BaseModel):
    id: int
    job_name: str
    status: str
    started_at: datetime
    ended_at: datetime | None
    duration_s: float | None
    result_json: dict[str, Any] | None
    error: str | None


class TriggerResponse(BaseModel):
    job_name: str
    accepted: bool
    detail: str


@router.get("/health", response_model=HealthStatus)
def health() -> HealthStatus:
    settings = get_settings()
    with get_session() as session:
        last_bar = session.execute(select(func.max(BarDaily.date))).scalar_one_or_none()
        bar_count = session.execute(select(func.count()).select_from(BarDaily)).scalar_one()

    return HealthStatus(
        status="ok",
        app_env=settings.app_env,
        server_time_utc=utcnow().isoformat(),
        database_url_scheme=settings.database_url.split(":", 1)[0],
        last_bar_date=last_bar,
        bar_count=int(bar_count),
    )


@router.get("/job-runs", response_model=list[JobRunOut])
def list_job_runs(
    limit: int = Query(default=50, ge=1, le=500),
    job_name: str | None = Query(default=None),
) -> list[JobRunOut]:
    with get_session() as session:
        stmt = select(JobRun).order_by(JobRun.id.desc()).limit(limit)
        if job_name:
            stmt = stmt.where(JobRun.job_name == job_name)
        rows = session.execute(stmt).scalars().all()
        return [_to_out(row) for row in rows]


@router.post("/jobs/{name}/run", response_model=TriggerResponse, status_code=202)
def trigger_job(name: str, background: BackgroundTasks) -> TriggerResponse:
    body = get_job_body(name)
    if body is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {name}")

    background.add_task(_run_in_thread, body)
    return TriggerResponse(job_name=name, accepted=True, detail="queued")


def _run_in_thread(body: Any) -> None:
    """Run the job body off the event loop so it doesn't block FastAPI."""
    threading.Thread(target=body, daemon=True).start()


def _to_out(row: JobRun) -> JobRunOut:
    duration: float | None = None
    if row.started_at is not None and row.ended_at is not None:
        duration = (row.ended_at - row.started_at).total_seconds()
    return JobRunOut(
        id=row.id,
        job_name=row.job_name,
        status=row.status,
        started_at=row.started_at,
        ended_at=row.ended_at,
        duration_s=duration,
        result_json=row.result_json,
        error=row.error,
    )
