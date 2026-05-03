"""Tests for the alert history routes — list, types, ack toggle."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from alembic import command
from db.models.alerts import Alert


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    db_path = tmp_path / "alerts_history.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")

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

    from api.main import app

    with TestClient(app) as c:
        yield c

    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()


def _seed_alerts(rows: list[Alert]) -> None:
    from db import get_session

    with get_session() as session:
        for row in rows:
            session.add(row)
        session.commit()


def test_list_alerts_returns_recent_first(client: TestClient) -> None:
    base = datetime(2026, 5, 1, 12, 0, 0)
    _seed_alerts(
        [
            Alert(
                alert_type="morning_digest",
                symbol=None,
                payload_json={"as_of": "2026-05-01"},
                triggered_at=base,
                channels_sent=json.dumps(["telegram"]),
            ),
            Alert(
                alert_type="iv_spike",
                symbol="AAPL",
                payload_json={"symbol": "AAPL"},
                triggered_at=base + timedelta(hours=1),
                channels_sent=json.dumps(["telegram"]),
            ),
        ]
    )

    response = client.get("/api/alerts")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["alert_type"] == "iv_spike"
    assert body[0]["symbol"] == "AAPL"
    assert body[0]["channels_sent"] == ["telegram"]
    assert body[0]["user_acked"] is False
    assert body[1]["alert_type"] == "morning_digest"


def test_list_alerts_filters(client: TestClient) -> None:
    base = datetime(2026, 5, 1, 12, 0, 0)
    _seed_alerts(
        [
            Alert(
                alert_type="morning_digest",
                payload_json={},
                triggered_at=base,
            ),
            Alert(
                alert_type="iv_spike",
                symbol="AAPL",
                payload_json={},
                triggered_at=base + timedelta(hours=1),
            ),
            Alert(
                alert_type="iv_spike",
                symbol="MSFT",
                payload_json={},
                triggered_at=base + timedelta(hours=2),
            ),
        ]
    )

    by_type = client.get("/api/alerts", params={"alert_type": "iv_spike"}).json()
    assert {row["symbol"] for row in by_type} == {"AAPL", "MSFT"}

    by_symbol = client.get("/api/alerts", params={"symbol": "aapl"}).json()
    assert len(by_symbol) == 1
    assert by_symbol[0]["symbol"] == "AAPL"

    since = (base + timedelta(hours=1, minutes=30)).isoformat()
    after = client.get("/api/alerts", params={"since": since}).json()
    assert len(after) == 1
    assert after[0]["symbol"] == "MSFT"


def test_list_alerts_pagination(client: TestClient) -> None:
    base = datetime(2026, 5, 1, 12, 0, 0)
    _seed_alerts(
        [
            Alert(
                alert_type="morning_digest",
                payload_json={"i": i},
                triggered_at=base + timedelta(minutes=i),
            )
            for i in range(5)
        ]
    )

    page1 = client.get("/api/alerts", params={"limit": 2}).json()
    page2 = client.get("/api/alerts", params={"limit": 2, "offset": 2}).json()
    assert len(page1) == 2
    assert len(page2) == 2
    assert page1[0]["payload"]["i"] == 4
    assert page2[0]["payload"]["i"] == 2


def test_list_alert_types(client: TestClient) -> None:
    base = datetime(2026, 5, 1, 12, 0, 0)
    _seed_alerts(
        [
            Alert(alert_type="morning_digest", payload_json={}, triggered_at=base),
            Alert(alert_type="morning_digest", payload_json={}, triggered_at=base),
            Alert(alert_type="iv_spike", payload_json={}, triggered_at=base),
        ]
    )

    types = client.get("/api/alerts/types").json()
    assert types == ["iv_spike", "morning_digest"]


def test_ack_toggle_round_trips(client: TestClient) -> None:
    _seed_alerts(
        [
            Alert(
                alert_type="morning_digest",
                payload_json={},
                triggered_at=datetime(2026, 5, 1, 12, 0, 0),
            )
        ]
    )

    from db import get_session

    with get_session() as session:
        alert_id = session.execute(select(Alert.id)).scalar_one()

    response = client.post(f"/api/alerts/{alert_id}/ack", json={"acked": True})
    assert response.status_code == 200
    assert response.json()["user_acked"] is True

    listed = client.get("/api/alerts").json()
    assert listed[0]["user_acked"] is True

    response = client.post(f"/api/alerts/{alert_id}/ack", json={"acked": False})
    assert response.status_code == 200
    assert response.json()["user_acked"] is False


def test_ack_unknown_alert_404(client: TestClient) -> None:
    response = client.post("/api/alerts/9999/ack", json={"acked": True})
    assert response.status_code == 404
