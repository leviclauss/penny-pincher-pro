"""Screener HTTP surface tests — configs and results.

Wires a TestClient against the FastAPI app with a per-test SQLite DB so the
asserts run against real persistence without mocks. Mirrors the pattern used
by ``test_tickers_api.py``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from db import get_engine, get_session, get_sessionmaker
from db.models.market import BarDaily, IndicatorDaily, Ticker
from db.models.screener import FilterConfig, ScreenerResult

AS_OF = date(2024, 6, 3)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    db_path = tmp_path / "screener_api.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")

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
    if "DATABASE_URL" in os.environ:
        # MonkeyPatch undoes this, but be paranoid for the next test.
        pass


def _seed_config(name: str, body: dict[str, Any], *, is_active: bool = True) -> int:
    with get_session() as session:
        config = FilterConfig(
            name=name,
            description=f"{name} desc",
            config_json=body,
            is_active=is_active,
        )
        session.add(config)
        session.commit()
        session.refresh(config)
        return config.id


def _seed_ticker_with_indicator(symbol: str, *, sector: str = "Tech") -> None:
    with get_session() as session:
        session.add(
            Ticker(
                symbol=symbol,
                name=f"{symbol} Inc.",
                sector=sector,
                market_cap=50_000_000_000.0,
                tier=1,
                is_active=True,
                is_hidden=False,
            )
        )
        session.add(
            BarDaily(symbol=symbol, date=AS_OF, open=100, high=100, low=100, close=100, volume=1)
        )
        session.add(
            IndicatorDaily(
                symbol=symbol,
                date=AS_OF,
                ema_200=98.0,
                rsi_14=32.0,
                iv_rank=70.0,
                iv_percentile=80.0,
            )
        )
        session.commit()


def _seed_result(
    symbol: str,
    config_id: int,
    *,
    passed: bool,
    score: float | None,
    filter_results: dict[str, Any] | None = None,
) -> None:
    with get_session() as session:
        session.add(
            ScreenerResult(
                date=AS_OF,
                symbol=symbol,
                config_id=config_id,
                passed=passed,
                score=score,
                filter_results_json=filter_results
                or {"near_200ema": {"value": 0.02, "passed": True, "eligible": True}},
            )
        )
        session.commit()


def test_list_configs_returns_active_and_inactive(client: TestClient) -> None:
    _seed_config("active", {"filters": [{"id": "rsi_oversold"}]}, is_active=True)
    _seed_config("paused", {"filters": [{"id": "near_200ema"}]}, is_active=False)

    response = client.get("/api/screener/configs")
    assert response.status_code == 200
    body = response.json()
    assert {c["name"] for c in body} == {"active", "paused"}
    active_only = client.get("/api/screener/configs", params={"active_only": True}).json()
    assert {c["name"] for c in active_only} == {"active"}


def test_get_config_returns_full_json(client: TestClient) -> None:
    body = {"filters": [{"id": "rsi_oversold", "params": {"max_rsi": 30}}]}
    config_id = _seed_config("primary", body)

    response = client.get(f"/api/screener/configs/{config_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["filter_ids"] == ["rsi_oversold"]
    assert payload["config_json"]["filters"][0]["params"]["max_rsi"] == 30


def test_get_config_404_when_missing(client: TestClient) -> None:
    response = client.get("/api/screener/configs/999")
    assert response.status_code == 404


def test_list_results_returns_passed_only_by_default(client: TestClient) -> None:
    config_id = _seed_config("c", {"filters": [{"id": "rsi_oversold"}]})
    _seed_ticker_with_indicator("AAA")
    _seed_ticker_with_indicator("BBB")
    _seed_result("AAA", config_id, passed=True, score=80.0)
    _seed_result("BBB", config_id, passed=False, score=None)

    response = client.get("/api/screener/results", params={"config_id": config_id})
    assert response.status_code == 200
    body = response.json()
    assert body["date"] == AS_OF.isoformat()
    assert [r["symbol"] for r in body["rows"]] == ["AAA"]
    assert body["rows"][0]["score"] == 80.0
    assert body["rows"][0]["sector"] == "Tech"
    assert body["rows"][0]["rsi_14"] == 32.0


def test_list_results_orders_by_score_desc(client: TestClient) -> None:
    config_id = _seed_config("c", {"filters": [{"id": "rsi_oversold"}]})
    _seed_ticker_with_indicator("AAA")
    _seed_ticker_with_indicator("BBB")
    _seed_ticker_with_indicator("CCC")
    _seed_result("AAA", config_id, passed=True, score=50.0)
    _seed_result("BBB", config_id, passed=True, score=90.0)
    _seed_result("CCC", config_id, passed=True, score=70.0)

    body = client.get("/api/screener/results", params={"config_id": config_id}).json()
    assert [r["symbol"] for r in body["rows"]] == ["BBB", "CCC", "AAA"]


def test_list_results_includes_failures_when_passed_only_false(client: TestClient) -> None:
    config_id = _seed_config("c", {"filters": [{"id": "rsi_oversold"}]})
    _seed_ticker_with_indicator("AAA")
    _seed_ticker_with_indicator("BBB")
    _seed_result("AAA", config_id, passed=True, score=80.0)
    _seed_result("BBB", config_id, passed=False, score=None)

    body = client.get(
        "/api/screener/results",
        params={"config_id": config_id, "passed_only": False},
    ).json()
    assert {r["symbol"] for r in body["rows"]} == {"AAA", "BBB"}


def test_list_results_uses_first_active_config_when_unspecified(client: TestClient) -> None:
    first = _seed_config("first", {"filters": [{"id": "rsi_oversold"}]})
    _seed_config("second", {"filters": [{"id": "near_200ema"}]})
    _seed_ticker_with_indicator("AAA")
    _seed_result("AAA", first, passed=True, score=42.0)

    body = client.get("/api/screener/results").json()
    assert body["config_id"] == first
    assert body["rows"][0]["score"] == 42.0


def test_list_results_returns_empty_when_no_data(client: TestClient) -> None:
    config_id = _seed_config("c", {"filters": [{"id": "rsi_oversold"}]})
    body = client.get("/api/screener/results", params={"config_id": config_id}).json()
    assert body["rows"] == []


def test_filter_catalog_returns_one_entry_per_registered_filter(
    client: TestClient,
) -> None:
    from screener.registry import FILTER_REGISTRY

    response = client.get("/api/screener/filters")
    assert response.status_code == 200
    body = response.json()
    assert {entry["id"] for entry in body} == set(FILTER_REGISTRY.keys())
    # Sorted by ID so the UI doesn't have to.
    assert [entry["id"] for entry in body] == sorted(FILTER_REGISTRY.keys())


def test_filter_catalog_serializes_param_schema(client: TestClient) -> None:
    body = client.get("/api/screener/filters").json()
    by_id = {entry["id"]: entry for entry in body}

    near_200ema = by_id["near_200ema"]
    assert near_200ema["category"] == "trend"
    assert near_200ema["scored"] is True
    assert near_200ema["label"]
    [param] = near_200ema["params"]
    assert param == {
        "name": "max_pct",
        "label": "Max distance from 200 EMA",
        "kind": "percent",
        "default": 0.03,
        "min": 0.0,
        "max": 0.5,
        "step": 0.005,
        "description": None,
    }

    # tier_set kind serializes its tuple default as a JSON list.
    tier_allowed = by_id["tier_allowed"]
    [tier_param] = tier_allowed["params"]
    assert tier_param["kind"] == "tier_set"
    assert tier_param["default"] == [1, 2]


def test_filter_catalog_lists_paramless_filters(client: TestClient) -> None:
    body = client.get("/api/screener/filters").json()
    by_id = {entry["id"]: entry for entry in body}
    assert by_id["weekly_above_200ema"]["params"] == []
    assert by_id["bb_lower_touch"]["params"] == []


def test_symbol_history_returns_recent_rows(client: TestClient) -> None:
    config_id = _seed_config("c", {"filters": [{"id": "rsi_oversold"}]})
    _seed_ticker_with_indicator("AAA")
    today = date.today()
    with get_session() as session:
        for offset, score in enumerate([60.0, 70.0, 80.0]):
            session.add(
                ScreenerResult(
                    date=today - timedelta(days=2 - offset),
                    symbol="AAA",
                    config_id=config_id,
                    passed=True,
                    score=score,
                    filter_results_json={},
                )
            )
        session.commit()

    response = client.get("/api/screener/results/AAA", params={"config_id": config_id})
    assert response.status_code == 200
    history = response.json()
    assert [r["score"] for r in history] == [80.0, 70.0, 60.0]
