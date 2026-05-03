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


# ---------------------------------------------------------------------------
# Write endpoints (PR2 — docs/planning/11-screener-config-ui.md)
# ---------------------------------------------------------------------------


def _valid_create_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "My Wheel",
        "description": "Conservative wheel candidates",
        "is_active": True,
        "filters": [
            {"id": "weekly_above_200ema", "required": True},
            {"id": "near_200ema", "params": {"max_pct": 0.04}},
            {"id": "iv_percentile_high", "params": {"min": 60}},
            {"id": "no_earnings_in_window", "params": {"days": 30}, "required": True},
            {"id": "tier_allowed", "params": {"tiers": [1, 2]}},
            {"id": "sector_concentration", "params": {"max": 3}},
        ],
        "scoring": {
            "weights": {
                "near_200ema": 0.5,
                "iv_percentile_high": 0.5,
            }
        },
    }
    body.update(overrides)
    return body


def test_create_config_persists_and_returns_detail(client: TestClient) -> None:
    response = client.post("/api/screener/configs", json=_valid_create_body())
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "My Wheel"
    assert body["is_active"] is True
    assert body["filter_ids"] == [
        "weekly_above_200ema",
        "near_200ema",
        "iv_percentile_high",
        "no_earnings_in_window",
        "tier_allowed",
        "sector_concentration",
    ]
    # Round-trip: GET returns the same config_json.
    fetched = client.get(f"/api/screener/configs/{body['id']}").json()
    assert fetched["config_json"]["filters"][1] == {
        "id": "near_200ema",
        "params": {"max_pct": 0.04},
    }
    assert fetched["config_json"]["filters"][0] == {
        "id": "weekly_above_200ema",
        "required": True,
    }
    assert fetched["config_json"]["scoring"]["weights"]["near_200ema"] == 0.5


def test_create_config_409_on_duplicate_name(client: TestClient) -> None:
    client.post("/api/screener/configs", json=_valid_create_body(name="Dup"))
    response = client.post("/api/screener/configs", json=_valid_create_body(name="Dup"))
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_create_config_400_on_unknown_filter_id(client: TestClient) -> None:
    body = _valid_create_body(filters=[{"id": "bogus_filter"}])
    response = client.post("/api/screener/configs", json=body)
    assert response.status_code == 400
    assert "bogus_filter" in response.json()["detail"]


def test_create_config_400_on_unknown_param(client: TestClient) -> None:
    body = _valid_create_body(
        filters=[{"id": "near_200ema", "params": {"made_up_param": 0.5}}],
        scoring={"weights": {}},
    )
    response = client.post("/api/screener/configs", json=body)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "near_200ema" in detail and "made_up_param" in detail


def test_create_config_400_on_param_value_out_of_range(client: TestClient) -> None:
    # near_200ema.max_pct max=0.5
    body = _valid_create_body(
        filters=[{"id": "near_200ema", "params": {"max_pct": 0.99}}],
        scoring={"weights": {}},
    )
    response = client.post("/api/screener/configs", json=body)
    assert response.status_code == 400
    assert "above max" in response.json()["detail"]


def test_create_config_400_on_weight_referencing_absent_filter(
    client: TestClient,
) -> None:
    body = _valid_create_body(
        filters=[{"id": "near_200ema"}],
        scoring={"weights": {"rsi_oversold": 0.5}},
    )
    response = client.post("/api/screener/configs", json=body)
    assert response.status_code == 400
    assert "rsi_oversold" in response.json()["detail"]


def test_create_config_400_on_weight_referencing_postprocessor(
    client: TestClient,
) -> None:
    body = _valid_create_body(
        filters=[
            {"id": "near_200ema"},
            {"id": "sector_concentration", "params": {"max": 3}},
        ],
        scoring={"weights": {"sector_concentration": 0.3}},
    )
    response = client.post("/api/screener/configs", json=body)
    assert response.status_code == 400
    assert "postprocessor" in response.json()["detail"]


def test_create_config_400_on_duplicate_filter_id(client: TestClient) -> None:
    body = _valid_create_body(
        filters=[
            {"id": "near_200ema", "params": {"max_pct": 0.02}},
            {"id": "near_200ema", "params": {"max_pct": 0.04}},
        ],
        scoring={"weights": {}},
    )
    response = client.post("/api/screener/configs", json=body)
    assert response.status_code == 400
    assert "duplicate" in response.json()["detail"]


def test_create_config_400_on_invalid_sector_concentration_max(
    client: TestClient,
) -> None:
    body = _valid_create_body(
        filters=[
            {"id": "near_200ema"},
            {"id": "sector_concentration", "params": {"max": 0}},
        ],
        scoring={"weights": {}},
    )
    response = client.post("/api/screener/configs", json=body)
    assert response.status_code == 400
    assert "sector_concentration" in response.json()["detail"]


def test_create_config_422_on_empty_name(client: TestClient) -> None:
    response = client.post("/api/screener/configs", json=_valid_create_body(name=""))
    assert response.status_code == 422


def test_create_config_422_on_empty_filters(client: TestClient) -> None:
    response = client.post("/api/screener/configs", json=_valid_create_body(filters=[]))
    assert response.status_code == 422


def test_replace_config_updates_fields(client: TestClient) -> None:
    created = client.post("/api/screener/configs", json=_valid_create_body(name="Original")).json()

    updated_body = _valid_create_body(
        name="Renamed",
        description="updated",
        is_active=False,
        filters=[{"id": "rsi_oversold", "params": {"max_rsi": 25}}],
        scoring={"weights": {"rsi_oversold": 1.0}},
    )
    response = client.put(f"/api/screener/configs/{created['id']}", json=updated_body)
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Renamed"
    assert body["is_active"] is False
    assert body["filter_ids"] == ["rsi_oversold"]
    assert body["config_json"]["scoring"]["weights"] == {"rsi_oversold": 1.0}


def test_replace_config_404_when_missing(client: TestClient) -> None:
    response = client.put("/api/screener/configs/9999", json=_valid_create_body())
    assert response.status_code == 404


def test_replace_config_409_on_name_clash_with_another(client: TestClient) -> None:
    a = client.post("/api/screener/configs", json=_valid_create_body(name="A")).json()
    client.post("/api/screener/configs", json=_valid_create_body(name="B"))
    response = client.put(f"/api/screener/configs/{a['id']}", json=_valid_create_body(name="B"))
    assert response.status_code == 409


def test_replace_config_allows_keeping_same_name(client: TestClient) -> None:
    a = client.post("/api/screener/configs", json=_valid_create_body(name="A")).json()
    response = client.put(
        f"/api/screener/configs/{a['id']}",
        json=_valid_create_body(name="A", description="changed"),
    )
    assert response.status_code == 200
    assert response.json()["description"] == "changed"


def test_delete_config_204_when_no_results(client: TestClient) -> None:
    created = client.post("/api/screener/configs", json=_valid_create_body()).json()
    response = client.delete(f"/api/screener/configs/{created['id']}")
    assert response.status_code == 204
    # Subsequent GET is a 404.
    assert client.get(f"/api/screener/configs/{created['id']}").status_code == 404


def test_delete_config_409_when_results_reference_it(client: TestClient) -> None:
    created = client.post("/api/screener/configs", json=_valid_create_body()).json()
    _seed_ticker_with_indicator("AAA")
    _seed_result("AAA", created["id"], passed=True, score=80.0)

    response = client.delete(f"/api/screener/configs/{created['id']}")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["result_count"] == 1
    assert "deactivate" in detail["message"]
    # Config is still there.
    assert client.get(f"/api/screener/configs/{created['id']}").status_code == 200


def test_delete_config_cascade_removes_results(client: TestClient) -> None:
    created = client.post("/api/screener/configs", json=_valid_create_body()).json()
    _seed_ticker_with_indicator("AAA")
    _seed_result("AAA", created["id"], passed=True, score=80.0)

    response = client.delete(f"/api/screener/configs/{created['id']}", params={"cascade": True})
    assert response.status_code == 204
    with get_session() as session:
        remaining = session.query(ScreenerResult).count()
    assert remaining == 0


def test_delete_config_404_when_missing(client: TestClient) -> None:
    assert client.delete("/api/screener/configs/9999").status_code == 404


def test_patch_active_toggles_is_active(client: TestClient) -> None:
    created = client.post("/api/screener/configs", json=_valid_create_body(is_active=True)).json()
    response = client.patch(
        f"/api/screener/configs/{created['id']}/active",
        json={"is_active": False},
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    # And toggle back.
    again = client.patch(
        f"/api/screener/configs/{created['id']}/active",
        json={"is_active": True},
    )
    assert again.json()["is_active"] is True


def test_patch_active_404_when_missing(client: TestClient) -> None:
    response = client.patch("/api/screener/configs/9999/active", json={"is_active": False})
    assert response.status_code == 404


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
