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

log = get_logger(__name__)

JobBody = Callable[[], None]
JOB_REGISTRY: dict[str, Callable[[], JobBody]] = {}


def register_job(name: str, factory: Callable[[], JobBody]) -> None:
    """Register a job body so manual triggers can resolve it by name."""
    JOB_REGISTRY[name] = factory


def get_job_body(name: str) -> JobBody | None:
    factory = JOB_REGISTRY.get(name)
    return factory() if factory is not None else None


def create_and_start() -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    _register_evening(scheduler, settings.scheduler_evening_hour, settings.scheduler_evening_minute)
    scheduler.start()
    log.info(
        "scheduler.started",
        timezone=settings.timezone,
        evening_hour=settings.scheduler_evening_hour,
        evening_minute=settings.scheduler_evening_minute,
    )
    return scheduler


def shutdown(scheduler: BackgroundScheduler, *, wait: bool = True) -> None:
    scheduler.shutdown(wait=wait)
    log.info("scheduler.stopped")


def _register_evening(scheduler: BackgroundScheduler, hour: int, minute: int) -> None:
    scheduler.add_job(
        _evening_entry,
        trigger=CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute),
        id=EVENING_JOB_NAME,
        replace_existing=True,
    )
    register_job(EVENING_JOB_NAME, lambda: _evening_entry)


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
