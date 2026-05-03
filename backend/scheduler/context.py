"""``job_run`` context manager — every scheduled or manual job wraps with this.

Persists a row in ``job_runs`` per execution: start time, end time, status
(running → success | failure), arbitrary result metrics, and the exception
string on failure. Use ``set_result(...)`` inside the block to record
job-specific metrics; the manager handles the lifecycle and never lets an
exception escape the DB write.

Side-effects on entry/exit:
- Healthchecks.io heartbeat ping (start / success / fail), best-effort.
- On failure, dispatch a ``job_failed`` alert (one per job per day) so a
  silent overnight breakage surfaces on Telegram instead of waiting for
  the next morning digest.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session

from core import healthchecks
from core.logging import get_logger
from core.time import utcnow
from db.models.system import JobRun

log = get_logger(__name__)

STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILURE = "failure"


class JobRunContext:
    """Handle yielded by ``job_run``. Use ``set_result(...)`` to attach metrics."""

    def __init__(self, job_run: JobRun) -> None:
        self._job_run = job_run

    @property
    def id(self) -> int:
        return self._job_run.id

    def set_result(self, **fields: Any) -> None:
        """Merge ``fields`` into the row's ``result_json`` (called inside the block)."""
        existing = self._job_run.result_json or {}
        merged = {**existing, **fields}
        self._job_run.result_json = merged


@contextmanager
def job_run(session: Session, job_name: str) -> Iterator[JobRunContext]:
    """Wrap a job body — always writes a job_runs row, even on failure."""
    row = JobRun(job_name=job_name, started_at=utcnow(), status=STATUS_RUNNING)
    session.add(row)
    session.flush()
    session.commit()

    log.info("job.start", job=job_name, run_id=row.id)
    healthchecks.ping(job_name, "start", message=f"run_id={row.id}")
    handle = JobRunContext(row)
    try:
        yield handle
    except Exception as exc:
        row.status = STATUS_FAILURE
        row.error = f"{type(exc).__name__}: {exc}"
        row.ended_at = utcnow()
        session.commit()
        log.error("job.failure", job=job_name, run_id=row.id, error=row.error)
        healthchecks.ping(job_name, "fail", message=row.error or "")
        _dispatch_failure_alert(session, row)
        raise
    else:
        row.status = STATUS_SUCCESS
        row.ended_at = utcnow()
        session.commit()
        log.info(
            "job.success",
            job=job_name,
            run_id=row.id,
            duration_s=_duration_seconds(row),
            result=row.result_json,
        )
        healthchecks.ping(job_name, "success", message=f"run_id={row.id}")


def _dispatch_failure_alert(session: Session, row: JobRun) -> None:
    """Fire a ``job_failed`` alert. Imported lazily to avoid a circular import
    (``alerts.dispatcher`` reads the DB through ``db.get_session`` which in
    turn imports models that don't depend on scheduler — the lazy import is
    cheap insurance against future churn).
    """
    from alerts.triggers.job_failure import maybe_dispatch  # noqa: PLC0415

    maybe_dispatch(
        session,
        job_name=row.job_name,
        error=row.error or "(no error message)",
        started_at=row.started_at,
        run_id=row.id,
    )


def _duration_seconds(row: JobRun) -> float | None:
    if row.started_at is None or row.ended_at is None:
        return None
    return (row.ended_at - row.started_at).total_seconds()
