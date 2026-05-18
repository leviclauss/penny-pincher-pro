"""Tests for the /api/tickers router."""

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
    db_path = tmp_path / "tickers.db"
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


def _seed_basic(client: TestClient) -> None:
    from db import get_session
    from db.models.market import BarDaily, Earnings, IndicatorDaily, Ticker

    today = date.today()
    with get_session() as session:
        session.add_all(
            [
                Ticker(symbol="AAPL", name="Apple Inc.", tier=1, sector="Tech", is_active=True),
                Ticker(symbol="MSFT", name="Microsoft", tier=1, sector="Tech", is_active=True),
            ]
        )
        session.flush()
        for i, day_offset in enumerate(range(5, 0, -1)):
            d = today - timedelta(days=day_offset)
            session.add(
                BarDaily(
                    symbol="AAPL",
                    date=d,
                    open=100.0 + i,
                    high=101.0 + i,
                    low=99.0 + i,
                    close=100.5 + i,
                    volume=1000 + i,
                )
            )
            session.add(
                IndicatorDaily(
                    symbol="AAPL",
                    date=d,
                    ema_20=100.0 + i,
                    ema_50=99.0 + i,
                    ema_200=95.0 + i,
                    rsi_14=55.0 + i,
                    iv_atm=0.25 + 0.01 * i,
                    iv_rank=50.0 + i,
                    iv_percentile=60.0 + i,
                )
            )
        session.add(
            Earnings(symbol="AAPL", earnings_date=today + timedelta(days=10), time_of_day="amc")
        )


def test_list_tickers_empty(client: TestClient) -> None:
    resp = client.get("/api/tickers")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_sectors_returns_distinct_non_null_sorted(client: TestClient) -> None:
    from db import get_session
    from db.models.market import Ticker

    with get_session() as session:
        session.add_all(
            [
                Ticker(symbol="AAPL", name="Apple", sector="Technology", is_active=True),
                Ticker(symbol="MSFT", name="Microsoft", sector="Technology", is_active=True),
                Ticker(symbol="XOM", name="Exxon", sector="Energy", is_active=True),
                Ticker(symbol="SPY", name="SPY ETF", sector=None, is_active=True),
            ]
        )

    resp = client.get("/api/tickers/sectors")
    assert resp.status_code == 200
    assert resp.json() == ["Energy", "Technology"]


def test_list_sectors_empty_when_no_tickers(client: TestClient) -> None:
    resp = client.get("/api/tickers/sectors")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_tickers_with_data(client: TestClient) -> None:
    _seed_basic(client)
    resp = client.get("/api/tickers")
    assert resp.status_code == 200
    rows = resp.json()
    assert {r["symbol"] for r in rows} == {"AAPL", "MSFT"}
    aapl = next(r for r in rows if r["symbol"] == "AAPL")
    assert aapl["name"] == "Apple Inc."
    assert aapl["tier"] == 1
    assert aapl["is_hidden"] is False
    assert aapl["last_close"] == 104.5
    assert aapl["ema_200"] == 99.0
    assert aapl["iv_atm"] == pytest.approx(0.29)
    assert aapl["next_earnings_date"] is not None
    msft = next(r for r in rows if r["symbol"] == "MSFT")
    assert msft["last_close"] is None
    assert msft["next_earnings_date"] is None


def test_list_tickers_uses_indicator_at_latest_bar_date(client: TestClient) -> None:
    """The IV pass writes IV-only rows on non-trading days (no ema/rsi). The
    list endpoint must read the indicator row aligned to the latest BAR
    date, not the latest indicator date, otherwise ema_200/rsi_14 come back
    NULL on every active ticker."""
    from db import get_session
    from db.models.market import IndicatorDaily, Ticker

    _seed_basic(client)
    today = date.today()
    with get_session() as session:
        # Simulate the IV-pass writing IV-only rows for "today" and "tomorrow"
        # past the latest bar date.
        session.add(
            IndicatorDaily(
                symbol="AAPL",
                date=today,
                iv_atm=0.31,
            )
        )
        session.add(
            IndicatorDaily(
                symbol="AAPL",
                date=today + timedelta(days=1),
                iv_atm=0.32,
            )
        )
        # And a similar rogue row for MSFT (which has no bars, so it should
        # still come back with all-null indicator fields).
        session.add(Ticker(symbol="GOOG", name="Alphabet", tier=1, is_active=True))
        session.add(IndicatorDaily(symbol="GOOG", date=today, iv_atm=0.40))

    resp = client.get("/api/tickers")
    assert resp.status_code == 200
    aapl = next(r for r in resp.json() if r["symbol"] == "AAPL")
    assert aapl["ema_200"] == 99.0
    assert aapl["rsi_14"] == 59.0


def test_chart_returns_bars_with_indicators(client: TestClient) -> None:
    _seed_basic(client)
    resp = client.get("/api/tickers/AAPL/chart?range=1y")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 5
    assert rows[0]["close"] == 100.5
    assert rows[-1]["ema_200"] == 99.0


def test_chart_unknown_symbol_returns_404(client: TestClient) -> None:
    resp = client.get("/api/tickers/XYZ/chart")
    assert resp.status_code == 404


def test_chart_rejects_unknown_range(client: TestClient) -> None:
    _seed_basic(client)
    resp = client.get("/api/tickers/AAPL/chart?range=banana")
    assert resp.status_code == 400


def test_iv_history_returns_series(client: TestClient) -> None:
    _seed_basic(client)
    resp = client.get("/api/tickers/AAPL/iv-history?range=1y")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 5
    assert rows[0]["iv_atm"] == pytest.approx(0.25)
    assert rows[-1]["iv_rank"] == 54.0


def test_iv_history_unknown_symbol_returns_404(client: TestClient) -> None:
    resp = client.get("/api/tickers/XYZ/iv-history")
    assert resp.status_code == 404


def test_list_excludes_hidden_by_default(client: TestClient) -> None:
    from db import get_session
    from db.models.market import Ticker

    _seed_basic(client)
    with get_session() as session:
        msft = session.get(Ticker, "MSFT")
        assert msft is not None
        msft.is_hidden = True

    resp = client.get("/api/tickers")
    assert resp.status_code == 200
    assert {r["symbol"] for r in resp.json()} == {"AAPL"}


def test_list_includes_hidden_when_requested(client: TestClient) -> None:
    from db import get_session
    from db.models.market import Ticker

    _seed_basic(client)
    with get_session() as session:
        msft = session.get(Ticker, "MSFT")
        assert msft is not None
        msft.is_hidden = True

    resp = client.get("/api/tickers?include_hidden=true")
    assert resp.status_code == 200
    rows = resp.json()
    assert {r["symbol"] for r in rows} == {"AAPL", "MSFT"}
    msft_row = next(r for r in rows if r["symbol"] == "MSFT")
    assert msft_row["is_hidden"] is True
