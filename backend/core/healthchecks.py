"""Healthchecks.io heartbeat pings.

Per-job ping URL lives in the environment as ``HEALTHCHECKS_URL_<JOB_NAME>``
(uppercased), matching the convention in ``docs/deploy.md``. A job without
a configured URL silently no-ops, so this is safe to wire into every
``job_run`` regardless of deployment.

The healthchecks.io ping API:
    GET <url>          → success
    GET <url>/start    → mark the job as in-flight (optional)
    GET <url>/fail     → mark the run as failed
    GET <url>/log      → record a log line without changing status

Network errors are swallowed and logged at WARNING — a failed heartbeat
shouldn't fail the underlying job, and the operator already gets a
"missed heartbeat" alert from healthchecks.io itself when pings stop
arriving.
"""

from __future__ import annotations

import os
from typing import Literal

import httpx

from core.config import get_settings
from core.logging import get_logger

log = get_logger(__name__)

PingStatus = Literal["start", "success", "fail"]

_ENV_PREFIX = "HEALTHCHECKS_URL_"


def url_for(job_name: str) -> str | None:
    """Return the configured ping URL for ``job_name`` (or None if unset)."""
    value = os.environ.get(f"{_ENV_PREFIX}{job_name.upper()}")
    return value.strip() if value and value.strip() else None


def ping(job_name: str, status: PingStatus, *, message: str = "") -> None:
    """Best-effort heartbeat. Silently skips when disabled or unconfigured."""
    settings = get_settings()
    if not settings.healthchecks_enabled:
        return
    base = url_for(job_name)
    if base is None:
        return

    suffix = "" if status == "success" else f"/{status}"
    url = base.rstrip("/") + suffix
    try:
        # POST so we can carry an optional body (error text, run_id, etc.) —
        # healthchecks.io accepts both verbs and stores the body in the log.
        httpx.post(url, content=message.encode("utf-8"), timeout=settings.healthchecks_timeout_s)
    except Exception as exc:
        log.warning(
            "healthchecks.ping_failed",
            job=job_name,
            status=status,
            error=f"{type(exc).__name__}: {exc}",
        )
