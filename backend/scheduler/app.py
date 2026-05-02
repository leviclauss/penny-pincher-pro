"""APScheduler instance + job registration.

Single ``BackgroundScheduler`` lives in the FastAPI process. Jobs run in
worker threads (one job at a time per id, but different jobs can interleave),
which keeps the async event loop free for HTTP traffic.

Lifecycle: ``create_and_start()`` builds the scheduler, registers all jobs,
and starts it. ``shutdown()`` waits for in-flight jobs by default. Both are
called from the FastAPI lifespan.

Manual triggers (``POST /api/system/jobs/{name}/run``) bypass the scheduler
and call the job body directly in a background task — surfaced through the
same ``job_runs`` table.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from core.config import get_settings
from core.logging import get_logger
from db import get_session
from ingestion.alpaca_client import AlpacaClient, AlpacaDataError
from ingestion.options import ChainSource
from ingestion.options_client import AlpacaOptionsClient, AlpacaOptionsError
from scheduler.jobs.evening import JOB_NAME as EVENING_JOB_NAME
from scheduler.jobs.evening import run_evening_pipeline
from scheduler.jobs.screener import JOB_NAME as SCREENER_JOB_NAME
from scheduler.jobs.screener import run_screener_job

log = get_logger(__name__)

JobBody = Callable[[], None]


@dataclass(frozen=True)
class JobInfo:
    """Static metadata about a registered job — paired with live scheduler state at read time."""

    name: str
    description: str
    cron: str
    timezone: str
    schedule_human: str
    factory: Callable[[], JobBody]


@dataclass(frozen=True)
class JobStatus:
    """Snapshot of a registered job: metadata + live scheduler state."""

    info: JobInfo
    enabled: bool
    next_run_at: datetime | None


JOB_REGISTRY: dict[str, JobInfo] = {}


def register_job(
    name: str,
    *,
    factory: Callable[[], JobBody],
    description: str,
    cron: str,
    timezone: str,
    schedule_human: str,
) -> None:
    """Record metadata for a job so manual triggers and the UI can resolve it by name."""
    JOB_REGISTRY[name] = JobInfo(
        name=name,
        description=description,
        cron=cron,
        timezone=timezone,
        schedule_human=schedule_human,
        factory=factory,
    )


def get_job_body(name: str) -> JobBody | None:
    info = JOB_REGISTRY.get(name)
    return info.factory() if info is not None else None


def list_jobs(scheduler: BackgroundScheduler | None) -> list[JobStatus]:
    """Combine the static registry with live APScheduler state for every known job."""
    statuses: list[JobStatus] = []
    for name, info in JOB_REGISTRY.items():
        if scheduler is None:
            statuses.append(JobStatus(info=info, enabled=False, next_run_at=None))
            continue
        job = scheduler.get_job(name)
        if job is None:
            statuses.append(JobStatus(info=info, enabled=False, next_run_at=None))
        else:
            statuses.append(JobStatus(info=info, enabled=True, next_run_at=job.next_run_time))
    return statuses


def create_and_start() -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    _register_evening(
        scheduler,
        settings.scheduler_evening_hour,
        settings.scheduler_evening_minute,
        settings.timezone,
    )
    screener_hour, screener_minute = _add_minutes(
        settings.scheduler_evening_hour,
        settings.scheduler_evening_minute,
        settings.scheduler_screener_offset_minutes,
    )
    _register_screener(scheduler, screener_hour, screener_minute, settings.timezone)
    scheduler.start()
    log.info(
        "scheduler.started",
        timezone=settings.timezone,
        evening_hour=settings.scheduler_evening_hour,
        evening_minute=settings.scheduler_evening_minute,
        screener_hour=screener_hour,
        screener_minute=screener_minute,
    )
    return scheduler


def _add_minutes(hour: int, minute: int, delta_minutes: int) -> tuple[int, int]:
    total = (hour * 60 + minute + delta_minutes) % (24 * 60)
    return total // 60, total % 60


def shutdown(scheduler: BackgroundScheduler, *, wait: bool = True) -> None:
    scheduler.shutdown(wait=wait)
    log.info("scheduler.stopped")


def _register_evening(
    scheduler: BackgroundScheduler, hour: int, minute: int, timezone: str
) -> None:
    cron = f"{minute} {hour} * * mon-fri"
    schedule_human = f"Mon-Fri {hour:02d}:{minute:02d} {timezone}"
    scheduler.add_job(
        _evening_entry,
        trigger=CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute),
        id=EVENING_JOB_NAME,
        replace_existing=True,
    )
    register_job(
        EVENING_JOB_NAME,
        factory=lambda: _evening_entry,
        description=("Post-close pipeline: bars → indicators → options → IV → earnings → macro."),
        cron=cron,
        timezone=timezone,
        schedule_human=schedule_human,
    )


def _evening_entry() -> None:
    settings = get_settings()
    alpaca = build_alpaca_client()
    if alpaca is None:
        log.warning("evening_pipeline.no_alpaca_creds_skipped")
        return
    options = build_options_client()
    calendar = settings.market_calendar or None
    with get_session() as session:
        run_evening_pipeline(
            session,
            alpaca_client=alpaca,
            options_client=options,
            market_calendar=calendar,
        )


def _register_screener(
    scheduler: BackgroundScheduler, hour: int, minute: int, timezone: str
) -> None:
    cron = f"{minute} {hour} * * mon-fri"
    schedule_human = f"Mon-Fri {hour:02d}:{minute:02d} {timezone}"
    scheduler.add_job(
        _screener_entry,
        trigger=CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute),
        id=SCREENER_JOB_NAME,
        replace_existing=True,
    )
    register_job(
        SCREENER_JOB_NAME,
        factory=lambda: _screener_entry,
        description="Run every active filter config against the watchlist and persist results.",
        cron=cron,
        timezone=timezone,
        schedule_human=schedule_human,
    )


def _screener_entry() -> None:
    settings = get_settings()
    calendar = settings.market_calendar or None
    with get_session() as session:
        run_screener_job(session, market_calendar=calendar)


def build_alpaca_client() -> AlpacaClient | None:
    try:
        return AlpacaClient()
    except AlpacaDataError:
        return None


def build_options_client() -> ChainSource | None:
    try:
        return AlpacaOptionsClient()
    except AlpacaOptionsError:
        return None
