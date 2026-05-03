"""``job_failed`` trigger — dispatched from the ``job_run`` failure path.

The dispatcher persists the alert and fans out to configured channels;
this module just builds the payload and applies the per-day dedup so a
job that fails on every retry doesn't spam the channel.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from alerts import dispatcher as dispatcher_module
from alerts.triggers._dedup import already_dispatched_for_job_today
from core.logging import get_logger
from core.time import utcnow

log = get_logger(__name__)

ALERT_TYPE = "job_failed"


def maybe_dispatch(
    session: Session,
    *,
    job_name: str,
    error: str,
    started_at: datetime,
    run_id: int | None,
    now: datetime | None = None,
) -> None:
    """Fire a ``job_failed`` alert for ``job_name`` unless one already fired today.

    Best-effort: any exception during dispatch is logged and swallowed so
    a broken alerting path can never compound a job failure.
    """
    today = (now or utcnow()).date()
    try:
        if already_dispatched_for_job_today(session, job_name=job_name, today=today):
            log.info("job_failed.dedup_skip", job=job_name, as_of=today.isoformat())
            return

        payload = {
            "job_name": job_name,
            "error": error,
            "started_at": started_at.isoformat(timespec="seconds"),
            "as_of": today.isoformat(),
            "run_id": run_id,
        }
        dispatcher_module.dispatch(ALERT_TYPE, payload)
    except Exception as exc:
        log.error(
            "job_failed.dispatch_error",
            job=job_name,
            error=f"{type(exc).__name__}: {exc}",
        )
