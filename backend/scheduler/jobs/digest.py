"""Scheduled digest jobs (morning + evening).

Each job:
1. Skips on NYSE holidays.
2. Skips if a digest of the same type was already dispatched today (dedup
   so a manual trigger plus the scheduled run don't double-fire).
3. Skips with a logged reason if the most recent bar is too stale —
   stale digests are misleading.
4. Builds the payload via ``alerts.triggers.digest`` and fans it out
   through ``alerts.dispatcher.dispatch``.

Holiday + skip outcomes still write a ``job_runs`` row so failures are
distinguishable from "intentionally did nothing today."
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import pandas_market_calendars as mcal
from sqlalchemy.orm import Session

import alerts.dispatcher as dispatcher_module
from alerts.triggers._dedup import already_dispatched_for_as_of
from alerts.triggers._freshness import check_bar_freshness
from alerts.triggers.digest import (
    EVENING_DIGEST,
    MORNING_DIGEST,
    build_evening_digest_payload,
    build_morning_digest_payload,
)
from core.logging import get_logger
from core.time import market_today
from scheduler.context import job_run

log = get_logger(__name__)

MORNING_JOB_NAME = "morning_digest"
EVENING_JOB_NAME = "evening_digest"


def run_morning_digest(
    session: Session,
    *,
    market_calendar: str | None = None,
    as_of: date | None = None,
) -> None:
    today = as_of or market_today()
    _run_digest(
        session,
        job_name=MORNING_JOB_NAME,
        alert_type=MORNING_DIGEST,
        builder=build_morning_digest_payload,
        market_calendar=market_calendar,
        today=today,
    )


def run_evening_digest(
    session: Session,
    *,
    market_calendar: str | None = None,
    as_of: date | None = None,
) -> None:
    today = as_of or market_today()
    _run_digest(
        session,
        job_name=EVENING_JOB_NAME,
        alert_type=EVENING_DIGEST,
        builder=build_evening_digest_payload,
        market_calendar=market_calendar,
        today=today,
    )


def _run_digest(
    session: Session,
    *,
    job_name: str,
    alert_type: str,
    builder: Callable[..., dict[str, Any]],
    market_calendar: str | None,
    today: date,
) -> None:
    if market_calendar and not _is_trading_day(market_calendar, today):
        log.info(f"{job_name}.holiday_skip", date=str(today), calendar=market_calendar)
        with job_run(session, job_name) as ctx:
            ctx.set_result(skipped="holiday", date=today.isoformat())
        return

    with job_run(session, job_name) as ctx:
        if already_dispatched_for_as_of(session, alert_type, as_of=today):
            log.info(f"{job_name}.dedup_skip", date=str(today))
            ctx.set_result(skipped="already_dispatched", date=today.isoformat())
            return

        freshness = check_bar_freshness(session, today=today)
        if not freshness.fresh:
            latest = freshness.latest_bar_date.isoformat() if freshness.latest_bar_date else None
            log.warning(
                f"{job_name}.stale_skip",
                date=str(today),
                latest_bar=latest,
                max_age_days=freshness.max_age_days,
            )
            ctx.set_result(
                skipped="stale_data",
                date=today.isoformat(),
                latest_bar=latest,
            )
            return

        payload = builder(session, as_of=today)
        result = dispatcher_module.dispatch(alert_type, payload)
        ctx.set_result(
            as_of=today.isoformat(),
            channels_attempted=result.channels_attempted,
            channels_sent=result.channels_sent,
            skipped_reason=result.skipped_reason,
            screener_hits=len(payload.get("screener_hits", [])),
        )


def _is_trading_day(calendar_name: str, day: date) -> bool:
    calendar = mcal.get_calendar(calendar_name)
    schedule = calendar.schedule(start_date=day, end_date=day)
    return bool(not schedule.empty)
