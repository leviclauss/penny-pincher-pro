"""Nightly SQLite backup job.

Uses ``sqlite3.Connection.backup()`` (the SQLite online backup API) so the
copy is consistent even while WAL writes are in flight — a plain file copy
can corrupt under load.

Steps:
1. Resolve the live SQLite path from ``settings.database_url``. Non-SQLite
   URLs short-circuit with ``skipped="non_sqlite"`` so the job is safe to
   register everywhere.
2. Snapshot to ``<backup_dir>/penny_pincher_<YYYYMMDD_HHMMSS>.db``,
   creating ``backup_dir`` on first run.
3. Prune to the most recent ``backup_retention`` files.
4. If off-site upload is configured, hand the snapshot to
   ``upload_offsite``; off-site failures are logged but do not fail the
   job (the local snapshot is the primary recovery artefact).

Wraps everything in ``job_run`` so success / skip / failure all land in
``job_runs``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from core.config import Settings, get_settings
from core.logging import get_logger
from core.time import utcnow
from scheduler.context import job_run

log = get_logger(__name__)

JOB_NAME = "sqlite_backup"

_FILENAME_PREFIX = "penny_pincher_"
_FILENAME_SUFFIX = ".db"
_TIMESTAMP_FMT = "%Y%m%d_%H%M%S"


def run_backup(
    session: Session,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> None:
    """Run the nightly SQLite backup once.

    ``settings`` and ``now`` are injectable for tests; production callers
    pass nothing.
    """
    cfg = settings or get_settings()
    timestamp = (now or utcnow()).strftime(_TIMESTAMP_FMT)

    with job_run(session, JOB_NAME) as ctx:
        source_path = _resolve_sqlite_path(cfg.database_url)
        if source_path is None:
            log.info("sqlite_backup.non_sqlite_skipped", database_url=cfg.database_url)
            ctx.set_result(skipped="non_sqlite")
            return

        if not source_path.exists():
            log.warning("sqlite_backup.source_missing", path=str(source_path))
            ctx.set_result(skipped="source_missing", source=str(source_path))
            return

        backup_dir = Path(cfg.backup_dir)
        if not backup_dir.is_absolute():
            backup_dir = (source_path.parent / backup_dir).resolve()
        backup_dir.mkdir(parents=True, exist_ok=True)

        target = backup_dir / f"{_FILENAME_PREFIX}{timestamp}{_FILENAME_SUFFIX}"
        _sqlite_backup(source_path, target)
        size_bytes = target.stat().st_size
        log.info(
            "sqlite_backup.snapshot_written",
            target=str(target),
            size_bytes=size_bytes,
        )

        pruned = _prune_old_backups(backup_dir, keep=cfg.backup_retention)
        if pruned:
            log.info("sqlite_backup.pruned", count=len(pruned))

        offsite_status = "disabled"
        offsite_error: str | None = None
        if cfg.backup_offsite_enabled:
            try:
                offsite_status = upload_offsite(target, cfg)
            except Exception as exc:
                offsite_status = "failed"
                offsite_error = f"{type(exc).__name__}: {exc}"
                log.error(
                    "sqlite_backup.offsite_failed",
                    target=str(target),
                    error=offsite_error,
                )

        ctx.set_result(
            target=str(target),
            size_bytes=size_bytes,
            pruned=len(pruned),
            retention=cfg.backup_retention,
            offsite=offsite_status,
            offsite_error=offsite_error,
        )


def _resolve_sqlite_path(database_url: str) -> Path | None:
    """Extract the on-disk SQLite path from a SQLAlchemy URL, or None for non-SQLite."""
    if not database_url.startswith("sqlite"):
        return None
    # SQLAlchemy SQLite URL forms:
    #   sqlite:///relative/path.db        -> relative/path.db
    #   sqlite:////absolute/path.db       -> /absolute/path.db
    #   sqlite:///:memory:                -> in-memory; no file to back up
    _, _, tail = database_url.partition("sqlite:///")
    if not tail or tail.startswith(":memory:"):
        return None
    return Path(tail)


def _sqlite_backup(source: Path, target: Path) -> None:
    """Online backup via SQLite's backup API — safe under concurrent writes."""
    src = sqlite3.connect(str(source))
    try:
        dst = sqlite3.connect(str(target))
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _prune_old_backups(backup_dir: Path, *, keep: int) -> list[Path]:
    """Delete all but the most recent ``keep`` snapshot files. Returns the deletions."""
    keep = max(keep, 0)
    candidates = sorted(
        (
            p
            for p in backup_dir.iterdir()
            if p.is_file()
            and p.name.startswith(_FILENAME_PREFIX)
            and p.name.endswith(_FILENAME_SUFFIX)
        ),
        key=lambda p: p.name,
        reverse=True,
    )
    deletions = candidates[keep:]
    for path in deletions:
        path.unlink(missing_ok=True)
    return deletions


def upload_offsite(target: Path, settings: Settings) -> str:
    """Upload ``target`` to the configured off-site provider.

    Both ``s3`` and ``b2`` go through ``boto3.client("s3")`` — Backblaze B2
    speaks the S3 protocol when given its S3-compatible endpoint URL.
    Returns a short status string recorded in the job_runs metrics.

    Requires the ``backup-s3`` optional extra (``pip install -e .[backup-s3]``).
    """
    provider = (settings.backup_offsite_provider or "").lower()
    if provider in {"", "none"}:
        return "disabled"
    if provider not in {"s3", "b2"}:
        raise ValueError(f"unsupported off-site provider: {provider!r}")
    if not settings.backup_offsite_bucket:
        raise ValueError("backup_offsite_bucket must be set when off-site upload is enabled")
    # B2's S3 API is per-region; insisting on an endpoint URL avoids the
    # foot-gun of accidentally hitting AWS with B2 credentials.
    if provider == "b2" and not settings.backup_offsite_endpoint_url:
        raise ValueError("backup_offsite_endpoint_url is required for provider='b2'")

    try:
        import boto3  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "off-site backup requires the 'backup-s3' extra; "
            "install with `pip install -e .[backup-s3]`"
        ) from exc

    client_kwargs: dict[str, str] = {}
    if settings.backup_offsite_endpoint_url:
        client_kwargs["endpoint_url"] = settings.backup_offsite_endpoint_url
    if settings.backup_offsite_region:
        client_kwargs["region_name"] = settings.backup_offsite_region
    if settings.backup_offsite_access_key_id:
        client_kwargs["aws_access_key_id"] = settings.backup_offsite_access_key_id
    if settings.backup_offsite_secret_access_key:
        client_kwargs["aws_secret_access_key"] = settings.backup_offsite_secret_access_key

    client = boto3.client("s3", **client_kwargs)
    prefix = (settings.backup_offsite_prefix or "").strip("/")
    key = f"{prefix}/{target.name}" if prefix else target.name
    client.upload_file(str(target), settings.backup_offsite_bucket, key)
    log.info(
        "sqlite_backup.offsite_uploaded",
        provider=provider,
        bucket=settings.backup_offsite_bucket,
        key=key,
    )
    return "uploaded"
