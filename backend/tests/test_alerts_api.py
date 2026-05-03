"""Smoke test for POST /api/alerts/test.

Replaces the real Telegram channel with a fake so the endpoint can be
exercised end-to-end without network. Verifies that an ``alerts`` row is
written and the response surfaces the dispatch outcome.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from alembic import command
from alerts.channels.base import ChannelResult
from db.models.alerts import Alert


class FakeChannel:
    id = "telegram"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def send(
        self,
        alert_type: str,
        payload: dict[str, Any],
        *,
        alert_id: int | None = None,
    ) -> ChannelResult:
        self.calls.append((alert_type, payload))
        return ChannelResult(True, "1", None)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    db_path = tmp_path / "alerts.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tkn")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

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

    from alerts import dispatcher

    fake = FakeChannel()
    dispatcher.CHANNELS.clear()
    dispatcher.CHANNELS["telegram"] = fake

    from api.main import app

    with TestClient(app) as c:
        yield c

    dispatcher.reset_registry()
    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()


def test_test_endpoint_dispatches_canned_payload(client: TestClient) -> None:
    response = client.post("/api/alerts/test?channel=telegram")
    assert response.status_code == 200
    body = response.json()
    assert body["channel"] == "telegram"
    assert body["alert_type"] == "morning_digest"
    assert body["delivered"] is True
    assert body["channels_sent"] == ["telegram"]
    assert body["alert_id"] is not None

    from db import get_session

    with get_session() as session:
        row = session.execute(select(Alert).where(Alert.id == body["alert_id"])).scalar_one()
        assert row.alert_type == "morning_digest"
        assert row.payload_json["as_of"] == "2026-05-02"


def test_test_endpoint_unknown_channel_404(client: TestClient) -> None:
    response = client.post("/api/alerts/test?channel=fax")
    assert response.status_code == 404


def test_test_endpoint_unknown_alert_type_404(client: TestClient) -> None:
    response = client.post("/api/alerts/test?alert_type=does_not_exist")
    assert response.status_code == 404
