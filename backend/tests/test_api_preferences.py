"""Alert preferences HTTP surface tests.

Per-test SQLite DB migrated via alembic, mirroring the pattern in
``test_screener_api.py`` / ``test_alerts_dispatcher.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import time
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from alembic import command
from db import get_engine, get_session, get_sessionmaker
from db.models.alerts import AlertPreference


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    db_path = tmp_path / "preferences_api.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")

    from core.config import get_settings

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    from api.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


def test_list_returns_synthesized_defaults_when_no_rows(client: TestClient) -> None:
    response = client.get("/api/alerts/preferences")
    assert response.status_code == 200
    body = response.json()

    types = {row["alert_type"] for row in body}
    # Templates ship with these alert types today.
    assert {
        "morning_digest",
        "evening_digest",
        "position_management",
        "setup_triggered",
        "iv_spike",
    } <= types

    for row in body:
        assert row["channels"] == ["telegram"]
        assert row["enabled"] is True
        assert row["quiet_hours_start"] is None
        assert row["quiet_hours_end"] is None


def test_put_inserts_new_row_and_get_reflects_it(client: TestClient) -> None:
    payload = {
        "channels": ["telegram"],
        "enabled": True,
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "07:00",
    }
    response = client.put("/api/alerts/preferences/morning_digest", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["alert_type"] == "morning_digest"
    assert body["channels"] == ["telegram"]
    assert body["quiet_hours_start"] == "22:00"
    assert body["quiet_hours_end"] == "07:00"

    with get_session() as session:
        row = session.execute(
            select(AlertPreference).where(AlertPreference.alert_type == "morning_digest")
        ).scalar_one()
        assert row.enabled is True
        assert row.quiet_hours_start == time(22, 0)
        assert row.quiet_hours_end == time(7, 0)

    listed = client.get("/api/alerts/preferences").json()
    morning = next(r for r in listed if r["alert_type"] == "morning_digest")
    assert morning["quiet_hours_start"] == "22:00"


def test_put_updates_existing_row(client: TestClient) -> None:
    client.put(
        "/api/alerts/preferences/iv_spike",
        json={
            "channels": ["telegram"],
            "enabled": True,
            "quiet_hours_start": None,
            "quiet_hours_end": None,
        },
    )
    response = client.put(
        "/api/alerts/preferences/iv_spike",
        json={
            "channels": ["telegram"],
            "enabled": False,
            "quiet_hours_start": "09:30",
            "quiet_hours_end": "16:00",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["quiet_hours_start"] == "09:30"

    with get_session() as session:
        rows = (
            session.execute(select(AlertPreference).where(AlertPreference.alert_type == "iv_spike"))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].enabled is False


def test_put_unknown_channel_returns_422(client: TestClient) -> None:
    response = client.put(
        "/api/alerts/preferences/morning_digest",
        json={
            "channels": ["telegram", "carrier_pigeon"],
            "enabled": True,
            "quiet_hours_start": None,
            "quiet_hours_end": None,
        },
    )
    assert response.status_code == 422
    assert "carrier_pigeon" in response.json()["detail"]


def test_put_invalid_quiet_hours_returns_422(client: TestClient) -> None:
    response = client.put(
        "/api/alerts/preferences/morning_digest",
        json={
            "channels": ["telegram"],
            "enabled": True,
            "quiet_hours_start": "not-a-time",
            "quiet_hours_end": None,
        },
    )
    assert response.status_code == 422


def test_channels_endpoint_reports_telegram_unconfigured(client: TestClient) -> None:
    response = client.get("/api/system/channels")
    assert response.status_code == 200
    assert response.json() == {"telegram": False, "email": False, "ntfy": False}
