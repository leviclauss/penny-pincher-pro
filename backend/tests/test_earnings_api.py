"""Tests for the /api/earnings router."""

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
    db_path = tmp_path / "earnings.db"
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
    from db.models.market import Earnings, Ticker

    today = date.today()
    with get_session() as session:
        session.add_all(
            [
                Ticker(symbol="AAPL", name="Apple", tier=1, is_active=True),
                Ticker(symbol="MSFT", name="Microsoft", tier=1, is_active=True),
                Ticker(symbol="GME", name="GameStop", tier=3, is_active=False),
            ]
        )
        session.flush()
        session.add_all(
            [
                Earnings(symbol="AAPL", earnings_date=today + timedelta(days=2), time_of_day="amc"),
                Earnings(symbol="MSFT", earnings_date=today + timedelta(days=5), time_of_day="bmo"),
                Earnings(symbol="AAPL", earnings_date=today + timedelta(days=20)),
                Earnings(symbol="GME", earnings_date=today + timedelta(days=3)),
                Earnings(symbol="AAPL", earnings_date=today - timedelta(days=1)),
            ]
        )


def test_upcoming_empty(client: TestClient) -> None:
    resp = client.get("/api/earnings/upcoming")
    assert resp.status_code == 200
    assert resp.json() == []


def test_upcoming_filters_by_window_and_active(client: TestClient) -> None:
    _seed(client)
    resp = client.get("/api/earnings/upcoming?days=7")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert [(r["symbol"], r["time_of_day"]) for r in rows] == [
        ("AAPL", "amc"),
        ("MSFT", "bmo"),
    ]
    assert rows[0]["name"] == "Apple"


def test_upcoming_wider_window_includes_later_events(client: TestClient) -> None:
    _seed(client)
    resp = client.get("/api/earnings/upcoming?days=30")
    rows = resp.json()
    assert {r["symbol"] for r in rows} == {"AAPL", "MSFT"}
    assert len(rows) == 3


def test_upcoming_rejects_out_of_range_days(client: TestClient) -> None:
    resp = client.get("/api/earnings/upcoming?days=0")
    assert resp.status_code == 422
    resp = client.get("/api/earnings/upcoming?days=400")
    assert resp.status_code == 422
