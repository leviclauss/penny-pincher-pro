"""Tests for the ticker watchlist mutation endpoints (POST, PATCH, DELETE)."""

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
    db_path = tmp_path / "tickers_mut.db"
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

    # Stop the create endpoint from spawning a real backfill thread.
    from api import tickers as tickers_module

    monkeypatch.setattr(tickers_module, "_run_in_thread", lambda body: None)

    from api.main import app

    with TestClient(app) as c:
        yield c

    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()


def test_create_ticker_201_and_persists(client: TestClient) -> None:
    from db import get_session
    from db.models.market import Ticker

    resp = client.post("/api/tickers", json={"symbol": "nvda", "tier": 2})
    assert resp.status_code == 201
    body = resp.json()
    assert body["symbol"] == "NVDA"
    assert body["tier"] == 2
    assert body["is_active"] is True
    assert body["is_hidden"] is False
    assert body["last_close"] is None

    with get_session() as session:
        row = session.get(Ticker, "NVDA")
        assert row is not None
        assert row.is_active is True
        assert row.is_hidden is False


def test_create_ticker_conflict_returns_409(client: TestClient) -> None:
    client.post("/api/tickers", json={"symbol": "AAPL"})
    resp = client.post("/api/tickers", json={"symbol": "AAPL"})
    assert resp.status_code == 409


def test_create_ticker_invalid_symbol_returns_422(client: TestClient) -> None:
    resp = client.post("/api/tickers", json={"symbol": "1bad!"})
    assert resp.status_code == 422


def test_patch_hide_sets_is_hidden(client: TestClient) -> None:
    client.post("/api/tickers", json={"symbol": "AAPL"})
    resp = client.patch("/api/tickers/AAPL", json={"is_hidden": True})
    assert resp.status_code == 200
    assert resp.json()["is_hidden"] is True

    listing = client.get("/api/tickers").json()
    assert "AAPL" not in {r["symbol"] for r in listing}

    listing_all = client.get("/api/tickers?include_hidden=true").json()
    assert "AAPL" in {r["symbol"] for r in listing_all}


def test_patch_unhide(client: TestClient) -> None:
    client.post("/api/tickers", json={"symbol": "AAPL"})
    client.patch("/api/tickers/AAPL", json={"is_hidden": True})
    resp = client.patch("/api/tickers/AAPL", json={"is_hidden": False})
    assert resp.status_code == 200
    assert resp.json()["is_hidden"] is False

    listing = client.get("/api/tickers").json()
    assert "AAPL" in {r["symbol"] for r in listing}


def test_patch_unknown_returns_404(client: TestClient) -> None:
    resp = client.patch("/api/tickers/XYZ", json={"is_hidden": True})
    assert resp.status_code == 404


def test_delete_ticker_cleans_related_rows(client: TestClient) -> None:
    from db import get_session
    from db.models.market import (
        BarDaily,
        Earnings,
        IndicatorDaily,
        OptionsSnapshot,
        Ticker,
    )

    client.post("/api/tickers", json={"symbol": "AAPL"})
    today = date.today()
    with get_session() as session:
        session.add(
            BarDaily(
                symbol="AAPL",
                date=today,
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                volume=100,
            )
        )
        session.add(IndicatorDaily(symbol="AAPL", date=today, ema_20=1.0, iv_atm=0.3))
        session.add(
            OptionsSnapshot(
                symbol="AAPL",
                expiration=today + timedelta(days=30),
                strike=100.0,
                option_type="call",
                bid=1.0,
                ask=1.05,
            )
        )
        session.add(
            Earnings(symbol="AAPL", earnings_date=today + timedelta(days=14), time_of_day="amc")
        )

    resp = client.delete("/api/tickers/AAPL")
    assert resp.status_code == 204

    from sqlalchemy import func, select

    with get_session() as session:
        assert session.get(Ticker, "AAPL") is None
        assert (
            session.execute(
                select(func.count()).select_from(BarDaily).where(BarDaily.symbol == "AAPL")
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                select(func.count())
                .select_from(IndicatorDaily)
                .where(IndicatorDaily.symbol == "AAPL")
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                select(func.count())
                .select_from(OptionsSnapshot)
                .where(OptionsSnapshot.symbol == "AAPL")
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                select(func.count()).select_from(Earnings).where(Earnings.symbol == "AAPL")
            ).scalar_one()
            == 0
        )


def test_delete_unknown_returns_404(client: TestClient) -> None:
    resp = client.delete("/api/tickers/XYZ")
    assert resp.status_code == 404
