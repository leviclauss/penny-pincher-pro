"""End-to-end test for the digest scheduler jobs.

Verifies:
- Holiday short-circuit writes a ``skipped="holiday"`` job_runs row.
- Stale-data guard skips dispatch when the latest bar is older than the
  freshness window.
- Dedup guard skips a second run on the same calendar day.
- Happy path dispatches via a fake channel and writes one ``alerts`` row
  + one ``job_runs`` row with the dispatch outcome.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import select

from alembic import command
from alerts.channels.base import Channel, ChannelResult
from db import get_session
from db.models.alerts import Alert
from db.models.market import BarDaily, Ticker
from db.models.system import JobRun
from scheduler.jobs.digest import (
    EVENING_JOB_NAME,
    MORNING_JOB_NAME,
    run_evening_digest,
    run_morning_digest,
)

AS_OF = date(2026, 5, 4)  # Monday


class _FakeChannel:
    id = "telegram"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def send(self, alert_type: str, payload: dict[str, Any]) -> ChannelResult:
        self.calls.append((alert_type, payload))
        return ChannelResult(True, "msg-1", None)


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_path = tmp_path / "digest_job.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")

    from core.config import get_settings
    from db import session as db_session

    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()

    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    yield

    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()


def _seed_fresh_bar(at: date) -> None:
    with get_session() as session:
        session.add(
            Ticker(
                symbol="AAPL",
                is_active=True,
                is_hidden=False,
                added_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        session.flush()
        session.add(
            BarDaily(
                symbol="AAPL",
                date=at,
                open=180.0,
                high=180.0,
                low=180.0,
                close=180.0,
                volume=1_000_000,
            )
        )


def _patch_channel(monkeypatch: pytest.MonkeyPatch) -> _FakeChannel:
    fake = _FakeChannel()
    registry: dict[str, Channel] = {"telegram": fake}
    monkeypatch.setattr("alerts.dispatcher.CHANNELS", registry)
    return fake


def _latest_jobrun(name: str) -> JobRun:
    with get_session() as session:
        row = (
            session.execute(
                select(JobRun).where(JobRun.job_name == name).order_by(JobRun.id.desc())
            )
            .scalars()
            .first()
        )
    assert row is not None
    return row


def test_morning_digest_holiday_skip(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_channel(monkeypatch)
    monkeypatch.setattr("scheduler.jobs.digest._is_trading_day", lambda *_args, **_kw: False)
    with get_session() as session:
        run_morning_digest(session, market_calendar="NYSE", as_of=AS_OF)

    job = _latest_jobrun(MORNING_JOB_NAME)
    assert job is not None
    assert (job.result_json or {}).get("skipped") == "holiday"

    with get_session() as session:
        assert session.execute(select(Alert)).first() is None


def test_morning_digest_stale_data_skip(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_channel(monkeypatch)
    _seed_fresh_bar(AS_OF - timedelta(days=10))  # well past 4-day window
    with get_session() as session:
        run_morning_digest(session, as_of=AS_OF)

    job = _latest_jobrun(MORNING_JOB_NAME)
    assert (job.result_json or {}).get("skipped") == "stale_data"

    with get_session() as session:
        assert session.execute(select(Alert)).first() is None


def test_morning_digest_dispatches_and_persists(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_channel(monkeypatch)
    _seed_fresh_bar(AS_OF)

    with get_session() as session:
        run_morning_digest(session, as_of=AS_OF)

    assert len(fake.calls) == 1
    alert_type, payload = fake.calls[0]
    assert alert_type == "morning_digest"
    assert payload["as_of"] == AS_OF.isoformat()

    with get_session() as session:
        alerts = session.execute(select(Alert)).scalars().all()
        assert len(alerts) == 1
        assert alerts[0].alert_type == "morning_digest"

    job = _latest_jobrun(MORNING_JOB_NAME)
    result = job.result_json or {}
    assert result["channels_sent"] == ["telegram"]


def test_morning_digest_dedup_skips_second_run(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_channel(monkeypatch)
    _seed_fresh_bar(AS_OF)

    with get_session() as session:
        run_morning_digest(session, as_of=AS_OF)
    with get_session() as session:
        run_morning_digest(session, as_of=AS_OF)

    # Only the first run dispatched.
    assert len(fake.calls) == 1

    job = _latest_jobrun(MORNING_JOB_NAME)
    assert (job.result_json or {}).get("skipped") == "already_dispatched"


def test_evening_digest_dispatches(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_channel(monkeypatch)
    _seed_fresh_bar(AS_OF)

    with get_session() as session:
        run_evening_digest(session, as_of=AS_OF)

    assert len(fake.calls) == 1
    assert fake.calls[0][0] == "evening_digest"

    job = _latest_jobrun(EVENING_JOB_NAME)
    assert (job.result_json or {}).get("channels_sent") == ["telegram"]
