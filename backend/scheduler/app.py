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

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from alerts.channels.telegram_inbound import TelegramInboundBot
from core.config import get_settings
from core.logging import get_logger
from db import get_session
from ingestion.alpaca_client import AlpacaClient, AlpacaDataError
from ingestion.options import ChainSource
from ingestion.options_client import AlpacaOptionsClient, AlpacaOptionsError
from scheduler.jobs.backup import JOB_NAME as BACKUP_JOB_NAME
from scheduler.jobs.backup import run_backup
from scheduler.jobs.digest import EVENING_JOB_NAME as EVENING_DIGEST_JOB_NAME
from scheduler.jobs.digest import MORNING_JOB_NAME as MORNING_DIGEST_JOB_NAME
from scheduler.jobs.digest import run_evening_digest, run_morning_digest
from scheduler.jobs.evening import JOB_NAME as EVENING_JOB_NAME
from scheduler.jobs.evening import run_evening_pipeline
from scheduler.jobs.intraday import JOB_NAME as INTRADAY_JOB_NAME
from scheduler.jobs.intraday import run_intraday_pulse
from scheduler.jobs.positions import JOB_NAME as POSITIONS_JOB_NAME
from scheduler.jobs.positions import run_position_management
from scheduler.jobs.screener import JOB_NAME as SCREENER_JOB_NAME
from scheduler.jobs.screener import run_screener_job
from scheduler.jobs.universe_scan import JOB_NAME as UNIVERSE_SCAN_JOB_NAME
from scheduler.jobs.universe_scan import run_universe_scan_job

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

# The inbound bot is a long-lived blocking loop — not an APScheduler job. We
# stash the live worker (and its bot handle) in a small mutable container so
# shutdown() can reach across and stop it cleanly without `global`.
_INBOUND_STATE: dict[str, Any] = {"thread": None, "bot": None}


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
    universe_hour, universe_minute = _add_minutes(
        settings.scheduler_evening_hour,
        settings.scheduler_evening_minute,
        settings.scheduler_universe_scan_offset_minutes,
    )
    _register_universe_scan(scheduler, universe_hour, universe_minute, settings.timezone)
    _register_positions(
        scheduler,
        settings.scheduler_positions_hour,
        settings.scheduler_positions_minute,
        settings.timezone,
    )
    _register_morning_digest(
        scheduler,
        settings.scheduler_morning_digest_hour,
        settings.scheduler_morning_digest_minute,
        settings.timezone,
    )
    _register_evening_digest(
        scheduler,
        settings.scheduler_evening_digest_hour,
        settings.scheduler_evening_digest_minute,
        settings.timezone,
    )
    if settings.scheduler_intraday_enabled:
        _register_intraday(
            scheduler,
            settings.scheduler_intraday_interval_minutes,
            settings.timezone,
        )
    _register_backup(
        scheduler,
        settings.scheduler_backup_hour,
        settings.scheduler_backup_minute,
        settings.timezone,
    )
    if settings.telegram_inbound_enabled:
        _start_inbound_bot()
    scheduler.start()
    log.info(
        "scheduler.started",
        timezone=settings.timezone,
        evening_hour=settings.scheduler_evening_hour,
        evening_minute=settings.scheduler_evening_minute,
        screener_hour=screener_hour,
        screener_minute=screener_minute,
        universe_scan_hour=universe_hour,
        universe_scan_minute=universe_minute,
        positions_hour=settings.scheduler_positions_hour,
        positions_minute=settings.scheduler_positions_minute,
        morning_digest_hour=settings.scheduler_morning_digest_hour,
        morning_digest_minute=settings.scheduler_morning_digest_minute,
        evening_digest_hour=settings.scheduler_evening_digest_hour,
        evening_digest_minute=settings.scheduler_evening_digest_minute,
        intraday_enabled=settings.scheduler_intraday_enabled,
        intraday_interval_minutes=settings.scheduler_intraday_interval_minutes,
        backup_hour=settings.scheduler_backup_hour,
        backup_minute=settings.scheduler_backup_minute,
    )
    return scheduler


def _add_minutes(hour: int, minute: int, delta_minutes: int) -> tuple[int, int]:
    total = (hour * 60 + minute + delta_minutes) % (24 * 60)
    return total // 60, total % 60


def shutdown(scheduler: BackgroundScheduler, *, wait: bool = True) -> None:
    _stop_inbound_bot()
    scheduler.shutdown(wait=wait)
    log.info("scheduler.stopped")


def _start_inbound_bot() -> None:
    """Spawn the long-poll inbound bot on a daemon thread.

    Idempotent: a second call while a thread is alive is a no-op so the
    FastAPI lifespan can call ``create_and_start`` more than once during
    test setup without leaking workers.
    """
    existing_thread = _INBOUND_STATE.get("thread")
    if isinstance(existing_thread, threading.Thread) and existing_thread.is_alive():
        return
    bot = TelegramInboundBot()
    if not bot.configured:
        log.warning("telegram_inbound.skip.unconfigured")
        return
    thread = threading.Thread(
        target=bot.run_forever,
        name="telegram-inbound-poller",
        daemon=True,
    )
    thread.start()
    _INBOUND_STATE["bot"] = bot
    _INBOUND_STATE["thread"] = thread
    log.info("telegram_inbound.thread_started")


def _stop_inbound_bot() -> None:
    bot = _INBOUND_STATE.get("bot")
    thread = _INBOUND_STATE.get("thread")
    if isinstance(bot, TelegramInboundBot):
        bot.stop()
    if isinstance(thread, threading.Thread) and thread.is_alive():
        # Long-poll can be up to ``telegram_inbound_long_poll_s`` long;
        # block briefly so we don't yank the connection mid-flight, then
        # let the daemon flag finish the cleanup if it's still pending.
        thread.join(timeout=2.0)
    _INBOUND_STATE["bot"] = None
    _INBOUND_STATE["thread"] = None


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


def _register_universe_scan(
    scheduler: BackgroundScheduler, hour: int, minute: int, timezone: str
) -> None:
    cron = f"{minute} {hour} * * mon-fri"
    schedule_human = f"Mon-Fri {hour:02d}:{minute:02d} {timezone}"
    scheduler.add_job(
        _universe_scan_entry,
        trigger=CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute),
        id=UNIVERSE_SCAN_JOB_NAME,
        replace_existing=True,
    )
    register_job(
        UNIVERSE_SCAN_JOB_NAME,
        factory=lambda: _universe_scan_entry,
        description=(
            "Sync S&P 100 universe tickers, then run the screener against them "
            "to surface option premium opportunities outside the watchlist."
        ),
        cron=cron,
        timezone=timezone,
        schedule_human=schedule_human,
    )


def _universe_scan_entry() -> None:
    settings = get_settings()
    calendar = settings.market_calendar or None
    with get_session() as session:
        run_universe_scan_job(session, market_calendar=calendar)


def _register_positions(
    scheduler: BackgroundScheduler, hour: int, minute: int, timezone: str
) -> None:
    cron = f"{minute} {hour} * * mon-fri"
    schedule_human = f"Mon-Fri {hour:02d}:{minute:02d} {timezone}"
    scheduler.add_job(
        _positions_entry,
        trigger=CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute),
        id=POSITIONS_JOB_NAME,
        replace_existing=True,
    )
    register_job(
        POSITIONS_JOB_NAME,
        factory=lambda: _positions_entry,
        description="Daily snapshot + management-rule pass for open wheel positions.",
        cron=cron,
        timezone=timezone,
        schedule_human=schedule_human,
    )


def _positions_entry() -> None:
    with get_session() as session:
        run_position_management(session)


def _register_morning_digest(
    scheduler: BackgroundScheduler, hour: int, minute: int, timezone: str
) -> None:
    cron = f"{minute} {hour} * * mon-fri"
    schedule_human = f"Mon-Fri {hour:02d}:{minute:02d} {timezone}"
    scheduler.add_job(
        _morning_digest_entry,
        trigger=CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute),
        id=MORNING_DIGEST_JOB_NAME,
        replace_existing=True,
    )
    register_job(
        MORNING_DIGEST_JOB_NAME,
        factory=lambda: _morning_digest_entry,
        description=(
            "Pre-open Telegram digest: macro, latest screener hits, today's earnings, positions."
        ),
        cron=cron,
        timezone=timezone,
        schedule_human=schedule_human,
    )


def _morning_digest_entry() -> None:
    settings = get_settings()
    calendar = settings.market_calendar or None
    with get_session() as session:
        run_morning_digest(session, market_calendar=calendar)


def _register_evening_digest(
    scheduler: BackgroundScheduler, hour: int, minute: int, timezone: str
) -> None:
    cron = f"{minute} {hour} * * mon-fri"
    schedule_human = f"Mon-Fri {hour:02d}:{minute:02d} {timezone}"
    scheduler.add_job(
        _evening_digest_entry,
        trigger=CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute),
        id=EVENING_DIGEST_JOB_NAME,
        replace_existing=True,
    )
    register_job(
        EVENING_DIGEST_JOB_NAME,
        factory=lambda: _evening_digest_entry,
        description=(
            "Post-close Telegram digest: today's screener hits, P&L summary, tomorrow's earnings."
        ),
        cron=cron,
        timezone=timezone,
        schedule_human=schedule_human,
    )


def _evening_digest_entry() -> None:
    settings = get_settings()
    calendar = settings.market_calendar or None
    with get_session() as session:
        run_evening_digest(session, market_calendar=calendar)


def _register_intraday(
    scheduler: BackgroundScheduler, interval_minutes: int, timezone: str
) -> None:
    cron = f"*/{interval_minutes} * * * mon-fri"
    schedule_human = f"Every {interval_minutes}m during RTH"
    scheduler.add_job(
        _intraday_entry,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id=INTRADAY_JOB_NAME,
        replace_existing=True,
    )
    register_job(
        INTRADAY_JOB_NAME,
        factory=lambda: _intraday_entry,
        description=(
            "Intraday alert pulse: setup_triggered + iv_spike. RTH-gated; off by default."
        ),
        cron=cron,
        timezone=timezone,
        schedule_human=schedule_human,
    )


def _intraday_entry() -> None:
    settings = get_settings()
    calendar = settings.market_calendar or None
    alpaca = build_alpaca_client()
    if alpaca is None:
        log.warning("intraday_pulse.no_alpaca_creds_skipped")
        return
    quote_source = alpaca.get_latest_quotes
    options = build_options_client() if settings.intraday_iv_spike_enabled else None
    chain_source = options.get_chain if options is not None else None
    with get_session() as session:
        run_intraday_pulse(
            session,
            quote_source=quote_source,
            chain_source=chain_source,
            market_calendar=calendar,
        )


def _register_backup(scheduler: BackgroundScheduler, hour: int, minute: int, timezone: str) -> None:
    cron = f"{minute} {hour} * * *"
    schedule_human = f"Daily {hour:02d}:{minute:02d} {timezone}"
    scheduler.add_job(
        _backup_entry,
        trigger=CronTrigger(hour=hour, minute=minute),
        id=BACKUP_JOB_NAME,
        replace_existing=True,
    )
    register_job(
        BACKUP_JOB_NAME,
        factory=lambda: _backup_entry,
        description=(
            "Nightly SQLite snapshot to backup_dir with retention pruning + optional off-site copy."
        ),
        cron=cron,
        timezone=timezone,
        schedule_human=schedule_human,
    )


def _backup_entry() -> None:
    with get_session() as session:
        run_backup(session)


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
