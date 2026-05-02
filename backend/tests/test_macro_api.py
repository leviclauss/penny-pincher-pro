"""Tests for the /api/macro router."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    db_path = tmp_path / "macro.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

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


def _seed(client: TestClient) -> None:
    from db import get_session
    from db.models.market import MacroDaily

    today = date.today()
    with get_session() as session:
        for i, off in enumerate([10, 5, 1]):
            d = today - timedelta(days=off)
            session.add(
                MacroDaily(
                    date=d,
                    vix_close=15.0 + i,
                    vix_9d=14.0 + i,
                    vix_term_structure=1.0 - 0.1 * i,
                    spy_close=500.0 + i,
                    spy_ema_200=480.0 + i,
                    spy_above_200ema=True,
                )
            )


def test_current_returns_null_when_empty(client: TestClient) -> None:
    resp = client.get("/api/macro/current")
    assert resp.status_code == 200
    assert resp.json() is None


def test_current_returns_latest(client: TestClient) -> None:
    _seed(client)
    resp = client.get("/api/macro/current")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["vix_close"] == 17.0
    assert payload["spy_above_200ema"] is True


def test_history_returns_series(client: TestClient) -> None:
    _seed(client)
    resp = client.get("/api/macro/history?range=6m")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 3
    assert rows[0]["vix_close"] == 15.0
    assert rows[-1]["vix_close"] == 17.0


def test_history_rejects_unknown_range(client: TestClient) -> None:
    resp = client.get("/api/macro/history?range=10y")
    assert resp.status_code == 400
