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
from datetime import UTC, date, datetime
from typing import Any, cast

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from core.config import get_settings
from core.time import market_today, utcnow
from db import get_session
from db.models.market import BarDaily, Ticker
from db.models.system import JobRun
from scheduler.app import get_job_body, list_jobs

router = APIRouter(prefix="/api/system", tags=["system"])


class HealthStatus(BaseModel):
    status: str
    app_env: str
    server_time_utc: str
    database_url_scheme: str
    last_bar_date: date | None
    bar_count: int


class TickerFreshness(BaseModel):
    symbol: str
    last_bar_date: date | None
    days_stale: int | None
    is_stale: bool


class DataFreshnessResponse(BaseModel):
    checked_at: str
    reference_date: date
    total_tickers: int
    stale_tickers: int
    fresh_tickers: int
    no_data_tickers: int
    tickers: list[TickerFreshness]


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


class JobInfoOut(BaseModel):
    name: str
    description: str
    schedule: str
    cron: str
    timezone: str
    enabled: bool
    next_run_at: datetime | None
    last_run: JobRunOut | None


class ChannelsStatus(BaseModel):
    telegram: bool
    email: bool
    ntfy: bool


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


@router.get("/channels", response_model=ChannelsStatus)
def channels_status() -> ChannelsStatus:
    settings = get_settings()
    return ChannelsStatus(
        telegram=bool(settings.telegram_bot_token and settings.telegram_chat_id),
        email=bool(settings.smtp_host and settings.smtp_from_address and settings.smtp_to_address),
        ntfy=bool(settings.ntfy_topic),
    )


@router.get("/data-freshness", response_model=DataFreshnessResponse)
def data_freshness(
    max_age_days: int = Query(default=3, ge=1, le=30),
) -> DataFreshnessResponse:
    """Per-ticker freshness status: staleness relative to today."""
    today = market_today()
    with get_session() as session:
        active_tickers = (
            session.execute(
                select(Ticker.symbol).where(Ticker.is_active.is_(True)).order_by(Ticker.symbol)
            )
            .scalars()
            .all()
        )

        # Latest bar date per symbol.
        latest_bars = dict(
            session.execute(
                select(BarDaily.symbol, func.max(BarDaily.date))
                .where(BarDaily.symbol.in_(active_tickers))
                .group_by(BarDaily.symbol)
            ).all()
        )

    tickers: list[TickerFreshness] = []
    stale_count = 0
    fresh_count = 0
    no_data_count = 0

    for symbol in active_tickers:
        last_bar_date = latest_bars.get(symbol)
        if last_bar_date is None:
            tickers.append(
                TickerFreshness(symbol=symbol, last_bar_date=None, days_stale=None, is_stale=False)
            )
            no_data_count += 1
        else:
            days_stale = (today - last_bar_date).days
            is_stale = days_stale > max_age_days
            tickers.append(
                TickerFreshness(
                    symbol=symbol,
                    last_bar_date=last_bar_date,
                    days_stale=days_stale,
                    is_stale=is_stale,
                )
            )
            if is_stale:
                stale_count += 1
            else:
                fresh_count += 1

    return DataFreshnessResponse(
        checked_at=utcnow().isoformat(),
        reference_date=today,
        total_tickers=len(active_tickers),
        stale_tickers=stale_count,
        fresh_tickers=fresh_count,
        no_data_tickers=no_data_count,
        tickers=tickers,
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


@router.get("/jobs", response_model=list[JobInfoOut])
def list_registered_jobs(request: Request) -> list[JobInfoOut]:
    scheduler = cast(
        BackgroundScheduler | None,
        request.app.state.__dict__.get("scheduler"),
    )
    statuses = list_jobs(scheduler)
    if not statuses:
        return []

    names = [s.info.name for s in statuses]
    with get_session() as session:
        latest_ids_subq = (
            select(func.max(JobRun.id).label("id"))
            .where(JobRun.job_name.in_(names))
            .group_by(JobRun.job_name)
            .subquery()
        )
        rows = (
            session.execute(select(JobRun).join(latest_ids_subq, JobRun.id == latest_ids_subq.c.id))
            .scalars()
            .all()
        )
        latest_by_name = {row.job_name: _to_out(row) for row in rows}

    return [
        JobInfoOut(
            name=s.info.name,
            description=s.info.description,
            schedule=s.info.schedule_human,
            cron=s.info.cron,
            timezone=s.info.timezone,
            enabled=s.enabled,
            next_run_at=s.next_run_at.astimezone(UTC) if s.next_run_at else None,
            last_run=latest_by_name.get(s.info.name),
        )
        for s in statuses
    ]


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
