"""Tests for the nightly SQLite backup job.

Verifies:
- The backup file is created via the SQLite online backup API and is a
  valid SQLite database that contains the original schema.
- A missing backup directory is created on first run.
- Retention prunes the oldest snapshots, keeping the configured count.
- Success path writes a job_runs row with the snapshot metrics.
- Non-SQLite database URLs short-circuit with a ``skipped="non_sqlite"``
  job_runs row.
- A failure mid-job is recorded as ``status="failure"`` in job_runs.
- Off-site upload errors are caught and recorded in job_runs without
  failing the job.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from core.config import Settings
from db.models.system import JobRun
from scheduler.context import STATUS_FAILURE, STATUS_SUCCESS
from scheduler.jobs import backup as backup_module
from scheduler.jobs.backup import JOB_NAME, run_backup


@pytest.fixture
def sqlite_db(tmp_path: Path) -> tuple[Path, Session]:
    """Migrated SQLite DB + an open Session pointed at it (the job's source DB)."""
    db_path = tmp_path / "live.db"
    url = f"sqlite:///{db_path}"
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return db_path, factory()


@pytest.fixture
def session(sqlite_db: tuple[Path, Session]) -> Iterator[Session]:
    _, s = sqlite_db
    try:
        yield s
    finally:
        s.close()


def _make_settings(
    *,
    db_path: Path,
    backup_dir: Path,
    retention: int = 14,
    offsite_enabled: bool = False,
    offsite_provider: str = "",
) -> Settings:
    return Settings(
        database_url=f"sqlite:///{db_path}",
        backup_dir=str(backup_dir),
        backup_retention=retention,
        backup_offsite_enabled=offsite_enabled,
        backup_offsite_provider=offsite_provider,
    )


def test_backup_creates_snapshot_and_records_jobrun(
    sqlite_db: tuple[Path, Session], tmp_path: Path
) -> None:
    db_path, s = sqlite_db
    backup_dir = tmp_path / "backups"  # intentionally missing — must be created
    settings = _make_settings(db_path=db_path, backup_dir=backup_dir)

    run_backup(s, settings=settings, now=datetime(2026, 5, 3, 3, 0, 0))

    assert backup_dir.exists()
    snapshots = sorted(backup_dir.glob("penny_pincher_*.db"))
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.name == "penny_pincher_20260503_030000.db"
    assert snapshot.stat().st_size > 0

    # Snapshot is a real SQLite DB with the schema applied.
    conn = sqlite3.connect(str(snapshot))
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    finally:
        conn.close()
    assert "job_runs" in tables

    row = s.execute(select(JobRun).where(JobRun.job_name == JOB_NAME)).scalar_one()
    assert row.status == STATUS_SUCCESS
    assert row.error is None
    assert row.result_json is not None
    assert row.result_json["target"] == str(snapshot)
    assert row.result_json["pruned"] == 0
    assert row.result_json["retention"] == 14
    assert row.result_json["offsite"] == "disabled"
    assert row.result_json["size_bytes"] > 0


def test_backup_prunes_to_retention(sqlite_db: tuple[Path, Session], tmp_path: Path) -> None:
    db_path, s = sqlite_db
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    # Seed older snapshots that should be pruned. Filenames sort
    # lexicographically by the embedded timestamp.
    for i in range(5):
        ts = (datetime(2026, 5, 1, 3, 0, 0) + timedelta(days=i)).strftime("%Y%m%d_%H%M%S")
        (backup_dir / f"penny_pincher_{ts}.db").write_bytes(b"stale")

    # Unrelated files must not be touched.
    (backup_dir / "README.txt").write_text("hands off")

    settings = _make_settings(db_path=db_path, backup_dir=backup_dir, retention=3)

    run_backup(s, settings=settings, now=datetime(2026, 5, 10, 3, 0, 0))

    remaining = sorted(p.name for p in backup_dir.glob("penny_pincher_*.db"))
    assert len(remaining) == 3
    # Newest 3 survive: today's run + two most-recent seeds.
    assert remaining == [
        "penny_pincher_20260504_030000.db",
        "penny_pincher_20260505_030000.db",
        "penny_pincher_20260510_030000.db",
    ]
    assert (backup_dir / "README.txt").exists()

    row = s.execute(select(JobRun).where(JobRun.job_name == JOB_NAME)).scalar_one()
    assert row.status == STATUS_SUCCESS
    assert (row.result_json or {})["pruned"] == 3


def test_backup_skips_non_sqlite(tmp_path: Path) -> None:
    # Use an in-memory SQLite engine to host the job_runs table; the job
    # itself is told the (fictitious) live DB is Postgres.
    backend_root = Path(__file__).resolve().parents[1]
    meta_url = f"sqlite:///{tmp_path / 'meta.db'}"
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", meta_url)
    command.upgrade(cfg, "head")
    engine = create_engine(meta_url)
    s = sessionmaker(bind=engine, expire_on_commit=False)()

    backup_dir = tmp_path / "backups"
    settings = Settings(
        database_url="postgresql://user:pw@localhost/wheel",
        backup_dir=str(backup_dir),
    )

    try:
        run_backup(s, settings=settings)
    finally:
        s.close()
        engine.dispose()

    s2 = sessionmaker(bind=create_engine(meta_url), expire_on_commit=False)()
    try:
        row = s2.execute(select(JobRun).where(JobRun.job_name == JOB_NAME)).scalar_one()
    finally:
        s2.close()
    assert row.status == STATUS_SUCCESS
    assert (row.result_json or {})["skipped"] == "non_sqlite"
    assert not backup_dir.exists()


def test_backup_records_failure_when_copy_raises(
    sqlite_db: tuple[Path, Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, s = sqlite_db
    backup_dir = tmp_path / "backups"
    settings = _make_settings(db_path=db_path, backup_dir=backup_dir)

    def boom(_src: Path, _dst: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(backup_module, "_sqlite_backup", boom)

    with pytest.raises(OSError, match="disk full"):
        run_backup(s, settings=settings)

    row = s.execute(select(JobRun).where(JobRun.job_name == JOB_NAME)).scalar_one()
    assert row.status == STATUS_FAILURE
    assert row.error is not None
    assert "disk full" in row.error


def test_backup_offsite_failure_does_not_fail_job(
    sqlite_db: tuple[Path, Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, s = sqlite_db
    backup_dir = tmp_path / "backups"
    settings = _make_settings(
        db_path=db_path,
        backup_dir=backup_dir,
        offsite_enabled=True,
        offsite_provider="s3",
    )

    def boom(_target: Path, _settings: Any) -> str:
        raise RuntimeError("network down")

    monkeypatch.setattr(backup_module, "upload_offsite", boom)

    run_backup(s, settings=settings, now=datetime(2026, 5, 3, 3, 0, 0))

    row = s.execute(select(JobRun).where(JobRun.job_name == JOB_NAME)).scalar_one()
    assert row.status == STATUS_SUCCESS
    result = row.result_json or {}
    assert result["offsite"] == "failed"
    assert result["offsite_error"] is not None
    assert "network down" in result["offsite_error"]
    # Local snapshot still landed.
    assert sorted(backup_dir.glob("penny_pincher_*.db"))


def test_backup_offsite_disabled_when_provider_blank(
    sqlite_db: tuple[Path, Session], tmp_path: Path
) -> None:
    db_path, s = sqlite_db
    backup_dir = tmp_path / "backups"
    # Enabled but no provider configured -> upload returns "disabled".
    settings = _make_settings(
        db_path=db_path,
        backup_dir=backup_dir,
        offsite_enabled=True,
        offsite_provider="",
    )

    run_backup(s, settings=settings, now=datetime(2026, 5, 3, 3, 0, 0))

    row = s.execute(select(JobRun).where(JobRun.job_name == JOB_NAME)).scalar_one()
    assert row.status == STATUS_SUCCESS
    assert (row.result_json or {})["offsite"] == "disabled"


def test_register_backup_job_is_resolvable() -> None:
    from scheduler.app import get_job_body

    body = get_job_body(JOB_NAME)
    # The job is registered eagerly via create_and_start; if it hasn't been
    # called in this test process we register it via a fresh scheduler.
    if body is None:
        from scheduler.app import create_and_start, shutdown

        scheduler = create_and_start()
        try:
            assert get_job_body(JOB_NAME) is not None
            assert scheduler.get_job(JOB_NAME) is not None
        finally:
            shutdown(scheduler)
    else:
        assert callable(body)
