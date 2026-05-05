"""HTTP surface tests for the backtest API.

Both modes are exercised end-to-end against an alembic-migrated SQLite DB:
the seeded universe is two upward-drifting synthetic tickers (puts always
expire OTM) so the strategy mode reaches the `csp_expired` branch and
`cycles_completed` lands non-zero. FastAPI's TestClient drains background
tasks before returning, so the polling pattern is exercised by re-fetching
the run after POST.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pandas_market_calendars as mcal
import pytest
from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from db import get_engine, get_session, get_sessionmaker
from db.models.market import BarDaily, IndicatorDaily, Ticker
from db.models.screener import FilterConfig

START = date(2024, 6, 3)
END = date(2024, 8, 30)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    db_path = tmp_path / "backtest_api.db"
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


def _trading_days(start: date, end: date) -> list[date]:
    schedule = mcal.get_calendar("NYSE").schedule(start_date=start, end_date=end)
    return [ts.date() for ts in schedule.index]


def _seed_universe() -> int:
    """Seed two upward-drifting symbols + a config that always passes them."""
    days = _trading_days(START, END)
    with get_session() as session:
        for symbol in ("AAA", "BBB"):
            session.add(
                Ticker(
                    symbol=symbol,
                    name=symbol,
                    sector="Tech",
                    is_active=True,
                    is_hidden=False,
                )
            )
        for symbol_idx, symbol in enumerate(("AAA", "BBB")):
            for i, d in enumerate(days):
                close = 100.0 + symbol_idx * 50.0 + i * 0.5
                session.add(
                    BarDaily(
                        symbol=symbol,
                        date=d,
                        open=close,
                        high=close,
                        low=close,
                        close=close,
                        volume=1,
                    )
                )
                session.add(IndicatorDaily(symbol=symbol, date=d, rsi_14=25.0, hv_20=0.30))
        config = FilterConfig(
            name="rsi-only",
            description="rsi-only",
            config_json={
                "filters": [{"id": "rsi_oversold", "params": {"max_rsi": 40}, "required": True}],
                "scoring": {"weights": {"rsi_oversold": 1.0}},
            },
            is_active=True,
        )
        session.add(config)
        session.flush()
        return config.id


def test_strategy_launcher_returns_202_and_completes_in_background(client: TestClient) -> None:
    config_id = _seed_universe()

    response = client.post(
        "/api/backtest/runs",
        json={
            "mode": "strategy",
            "config_id": config_id,
            "start_date": START.isoformat(),
            "end_date": END.isoformat(),
            "strategy_params": {
                "starting_capital": 50_000.0,
                "max_concurrent_positions": 2,
            },
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["mode"] == "strategy"
    # TestClient blocks until background tasks drain, so by the time we read
    # the run again it's already in its terminal state.
    run_id = body["id"]
    assert body["starting_capital"] == 50_000.0

    follow_up = client.get(f"/api/backtest/runs/{run_id}")
    assert follow_up.status_code == 200
    final = follow_up.json()
    assert final["status"] == "completed"
    assert final["error_message"] is None
    assert final["mode"] == "strategy"
    assert final["final_equity"] is not None
    assert final["total_return_pct"] is not None
    assert final["cycles_completed"] is not None
    # Filter-only stats stay None on strategy runs.
    assert final["mean_return_pct"] is None
    assert final["win_rate"] is None


def test_strategy_run_writes_equity_and_trade_rows(client: TestClient) -> None:
    config_id = _seed_universe()
    response = client.post(
        "/api/backtest/runs",
        json={
            "mode": "strategy",
            "config_id": config_id,
            "start_date": START.isoformat(),
            "end_date": END.isoformat(),
            "strategy_params": {"starting_capital": 25_000.0},
        },
    )
    run_id = response.json()["id"]

    equity = client.get(f"/api/backtest/runs/{run_id}/equity").json()
    assert len(equity) == len(_trading_days(START, END))
    # Ordered by date ascending.
    dates = [row["date"] for row in equity]
    assert dates == sorted(dates)
    assert all(row["equity"] > 0 for row in equity)

    trades = client.get(f"/api/backtest/runs/{run_id}/trades").json()
    assert trades, "strategy mode should write at least one csp_open trade"
    # Strategy trades expose dollar P&L (not pct) and carry leg/cycle metadata.
    leg_types = {t["leg_type"] for t in trades}
    assert "csp_open" in leg_types
    closed = [t for t in trades if t["exit_date"] is not None]
    if closed:
        assert closed[0]["realized_pnl"] is not None
        assert closed[0]["realized_pnl_pct"] is None
        assert closed[0]["cycle_id"] is not None


def test_strategy_launcher_threads_hold_losers_to_expiry(client: TestClient) -> None:
    """The API must propagate ``hold_losers_to_expiry`` into the persisted params.

    Regression: the API previously dropped fields not listed in
    ``StrategyParamsIn``, so the True Wheel preset ran with the default
    (False) regardless of what the UI sent.
    """
    config_id = _seed_universe()

    on = client.post(
        "/api/backtest/runs",
        json={
            "mode": "strategy",
            "config_id": config_id,
            "start_date": START.isoformat(),
            "end_date": END.isoformat(),
            "strategy_params": {"hold_losers_to_expiry": True},
        },
    )
    assert on.status_code == 202, on.text
    on_params = on.json()["params_json"]
    assert on_params["hold_losers_to_expiry"] is True

    off = client.post(
        "/api/backtest/runs",
        json={
            "mode": "strategy",
            "config_id": config_id,
            "start_date": START.isoformat(),
            "end_date": END.isoformat(),
        },
    )
    assert off.status_code == 202, off.text
    off_params = off.json()["params_json"]
    assert off_params["hold_losers_to_expiry"] is False


def test_failed_strategy_run_lands_failed_with_error_message(
    client: TestClient,
) -> None:
    """Bad date window should fail the run and persist the error_message."""
    config_id = _seed_universe()
    response = client.post(
        "/api/backtest/runs",
        json={
            "mode": "strategy",
            "config_id": config_id,
            # Window with no NYSE trading days (a single weekend).
            "start_date": "2024-06-08",
            "end_date": "2024-06-09",
        },
    )
    assert response.status_code == 202
    run_id = response.json()["id"]

    final = client.get(f"/api/backtest/runs/{run_id}").json()
    assert final["status"] == "failed"
    assert final["error_message"] is not None
    assert "trading day" in final["error_message"].lower()


def test_unknown_config_id_returns_400(client: TestClient) -> None:
    response = client.post(
        "/api/backtest/runs",
        json={
            "mode": "strategy",
            "config_id": 99999,
            "start_date": START.isoformat(),
            "end_date": END.isoformat(),
        },
    )
    assert response.status_code == 400
    assert "config not found" in response.json()["detail"]


def test_filter_mode_still_works_end_to_end(client: TestClient) -> None:
    config_id = _seed_universe()
    response = client.post(
        "/api/backtest/runs",
        json={
            "mode": "filter",
            "config_id": config_id,
            "start_date": START.isoformat(),
            "end_date": END.isoformat(),
            "forward_days": 5,
        },
    )
    assert response.status_code == 202
    run_id = response.json()["id"]

    final = client.get(f"/api/backtest/runs/{run_id}").json()
    assert final["mode"] == "filter"
    assert final["status"] == "completed"
    # Filter-mode metrics populated; strategy-only metrics stay None.
    assert final["trade_count"] > 0
    assert final["win_rate"] is not None
    assert final["mean_return_pct"] is not None
    assert final["final_equity"] is None
    assert final["cycles_completed"] is None

    # Filter trades expose realized_pnl_pct and leave realized_pnl unset.
    trades = client.get(f"/api/backtest/runs/{run_id}/trades").json()
    assert trades
    assert trades[0]["realized_pnl_pct"] is not None
    assert trades[0]["realized_pnl"] is None
    assert trades[0]["leg_type"] == "filter_pass"

    # Equity endpoint returns an empty list for filter runs.
    equity = client.get(f"/api/backtest/runs/{run_id}/equity").json()
    assert equity == []


def test_end_date_must_follow_start_date(client: TestClient) -> None:
    config_id = _seed_universe()
    response = client.post(
        "/api/backtest/runs",
        json={
            "mode": "strategy",
            "config_id": config_id,
            "start_date": END.isoformat(),
            "end_date": START.isoformat(),
        },
    )
    # pydantic model_validator rejects this before the route body runs.
    assert response.status_code == 422


def test_list_runs_orders_newest_first(client: TestClient) -> None:
    config_id = _seed_universe()
    for _ in range(2):
        client.post(
            "/api/backtest/runs",
            json={
                "mode": "filter",
                "config_id": config_id,
                "start_date": START.isoformat(),
                "end_date": END.isoformat(),
                "forward_days": 5,
            },
        )
    runs = client.get("/api/backtest/runs").json()
    assert len(runs) == 2
    assert runs[0]["created_at"] >= runs[1]["created_at"]
