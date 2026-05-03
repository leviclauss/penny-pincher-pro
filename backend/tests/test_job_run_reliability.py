"""Reliability hooks wired into ``job_run``: healthchecks.io heartbeat
pings and ``job_failed`` alert dispatch on the failure path.

The autouse fixture in ``conftest.py`` no-ops both hooks for every other
test in the suite. These tests opt back in via ``real_healthchecks_ping``
and ``real_maybe_dispatch``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from alerts import dispatcher as dispatcher_module
from core import healthchecks as healthchecks_module
from core.time import utcnow
from db.models.alerts import Alert
from scheduler.context import job_run


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "jobruns.db"
    url = f"sqlite:///{db_path}"
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


# --- Healthchecks heartbeats -------------------------------------------------


class _RecordingPoster:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, float]] = []

    def __call__(self, url: str, *, content: bytes, timeout: float) -> None:
        self.calls.append((url, content, timeout))


def test_healthcheck_ping_skipped_when_no_env_var(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    real_healthchecks_ping: Any,
) -> None:
    poster = _RecordingPoster()
    monkeypatch.setattr(httpx, "post", poster)
    monkeypatch.delenv("HEALTHCHECKS_URL_DEMO", raising=False)

    with job_run(session, "demo"):
        pass

    assert poster.calls == []


def test_healthcheck_ping_success_path(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    real_healthchecks_ping: Any,
) -> None:
    poster = _RecordingPoster()
    monkeypatch.setattr(httpx, "post", poster)
    monkeypatch.setenv("HEALTHCHECKS_URL_DEMO", "https://hc-ping.com/abc-123")

    with job_run(session, "demo"):
        pass

    urls = [call[0] for call in poster.calls]
    assert urls == ["https://hc-ping.com/abc-123/start", "https://hc-ping.com/abc-123"]


def test_healthcheck_ping_failure_path_carries_error(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    real_healthchecks_ping: Any,
) -> None:
    poster = _RecordingPoster()
    monkeypatch.setattr(httpx, "post", poster)
    monkeypatch.setenv("HEALTHCHECKS_URL_DEMO", "https://hc-ping.com/abc-123")

    with pytest.raises(RuntimeError, match="boom"), job_run(session, "demo"):
        raise RuntimeError("boom")

    urls = [call[0] for call in poster.calls]
    assert urls == ["https://hc-ping.com/abc-123/start", "https://hc-ping.com/abc-123/fail"]
    fail_body = poster.calls[-1][1].decode("utf-8")
    assert "RuntimeError: boom" in fail_body


def test_healthcheck_ping_swallows_network_errors(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    real_healthchecks_ping: Any,
) -> None:
    """A broken healthchecks endpoint must never propagate into the job."""

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise ConnectionError("dns failure")

    monkeypatch.setattr(httpx, "post", boom)
    monkeypatch.setenv("HEALTHCHECKS_URL_DEMO", "https://hc-ping.com/abc-123")

    with job_run(session, "demo"):
        pass


def test_healthcheck_global_disable_short_circuits(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    real_healthchecks_ping: Any,
) -> None:
    poster = _RecordingPoster()
    monkeypatch.setattr(httpx, "post", poster)
    monkeypatch.setenv("HEALTHCHECKS_URL_DEMO", "https://hc-ping.com/abc-123")

    fake_settings = type(
        "FakeSettings", (), {"healthchecks_enabled": False, "healthchecks_timeout_s": 5.0}
    )()
    monkeypatch.setattr(healthchecks_module, "get_settings", lambda: fake_settings)

    with job_run(session, "demo"):
        pass

    assert poster.calls == []


# --- job_failed alert --------------------------------------------------------


class _RecordingDispatcher:
    def __init__(self, *, raise_on_call: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.raise_on_call = raise_on_call

    def __call__(self, alert_type: str, payload: dict[str, Any]) -> None:
        self.calls.append((alert_type, payload))
        if self.raise_on_call:
            raise RuntimeError("downstream telegram offline")


def test_job_failure_dispatches_alert(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    real_maybe_dispatch: Any,
) -> None:
    recorder = _RecordingDispatcher()
    monkeypatch.setattr(dispatcher_module, "dispatch", recorder)

    with pytest.raises(RuntimeError, match="boom"), job_run(session, "evening_pipeline"):
        raise RuntimeError("boom")

    assert len(recorder.calls) == 1
    alert_type, payload = recorder.calls[0]
    assert alert_type == "job_failed"
    assert payload["job_name"] == "evening_pipeline"
    assert "RuntimeError: boom" in payload["error"]
    assert "as_of" in payload
    assert payload["run_id"] is not None


def test_job_failure_dedup_one_per_day(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    real_maybe_dispatch: Any,
) -> None:
    recorder = _RecordingDispatcher()
    monkeypatch.setattr(dispatcher_module, "dispatch", recorder)

    # Pre-seed an Alert row for today so the dedup helper finds it.
    today = utcnow().date()
    session.add(
        Alert(
            alert_type="job_failed",
            payload_json={"job_name": "evening_pipeline", "as_of": today.isoformat()},
            channels_sent=json.dumps([]),
        )
    )
    session.commit()

    with pytest.raises(RuntimeError, match="boom"), job_run(session, "evening_pipeline"):
        raise RuntimeError("boom")

    assert recorder.calls == []


def test_job_failure_dispatcher_exception_is_swallowed(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    real_maybe_dispatch: Any,
) -> None:
    recorder = _RecordingDispatcher(raise_on_call=True)
    monkeypatch.setattr(dispatcher_module, "dispatch", recorder)

    # The original exception still propagates; the dispatcher hiccup is
    # logged but does not mask the underlying job failure.
    with pytest.raises(RuntimeError, match="boom"), job_run(session, "evening_pipeline"):
        raise RuntimeError("boom")

    assert len(recorder.calls) == 1
