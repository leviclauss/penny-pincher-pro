"""Digest builder tests.

Covers:
- Empty DB → renderable empty payload.
- Populated DB → screener hits ranked, macro snapshot, attention list,
  next-earnings days computed off ``as_of``.
- The morning + evening digest payloads round-trip cleanly through the
  Telegram renderer (catches template/builder drift).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from alerts.templates.telegram_render import render
from alerts.triggers.digest import (
    EVENING_DIGEST,
    MORNING_DIGEST,
    build_evening_digest_payload,
    build_morning_digest_payload,
)
from db import get_session
from db.models.market import BarDaily, Earnings, IndicatorDaily, MacroDaily, Ticker
from db.models.screener import FilterConfig, ScreenerResult
from positions import state_machine as sm

AS_OF = date(2026, 5, 4)


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_path = tmp_path / "digest.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")

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

    yield

    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()


def _seed_macro() -> None:
    with get_session() as session:
        session.add(
            MacroDaily(
                date=AS_OF,
                vix_close=14.2,
                vix_9d=13.0,
                vix_term_structure=0.92,
                spy_close=520.0,
                spy_ema_200=480.0,
                spy_above_200ema=True,
            )
        )


def _seed_ticker(symbol: str) -> None:
    with get_session() as session:
        session.add(
            Ticker(
                symbol=symbol,
                is_active=True,
                is_hidden=False,
                added_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )


def _seed_screener_hit(
    *,
    symbol: str,
    config_name: str,
    score: float,
    rsi: float,
    iv_percentile: float,
    close: float,
    next_earnings_in_days: int | None,
) -> int:
    with get_session() as session:
        config = FilterConfig(
            name=config_name,
            description=None,
            config_json={"filters": []},
            is_active=True,
        )
        session.add(config)
        session.flush()
        session.add(
            ScreenerResult(
                date=AS_OF,
                symbol=symbol,
                config_id=config.id,
                passed=True,
                score=score,
            )
        )
        session.add(
            BarDaily(
                symbol=symbol,
                date=AS_OF,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1_000_000,
            )
        )
        session.add(
            IndicatorDaily(
                symbol=symbol,
                date=AS_OF,
                rsi_14=rsi,
                iv_percentile=iv_percentile,
            )
        )
        if next_earnings_in_days is not None:
            session.add(
                Earnings(
                    symbol=symbol,
                    earnings_date=AS_OF + timedelta(days=next_earnings_in_days),
                    time_of_day="AMC",
                )
            )
        config_id = config.id
    return config_id


def test_morning_digest_empty_db_returns_empty_lists(db: None) -> None:
    with get_session() as session:
        payload = build_morning_digest_payload(session, as_of=AS_OF)

    assert payload["as_of"] == "2026-05-04"
    assert payload["screener_hits"] == []
    assert payload["earnings_today"] == []
    assert payload["positions_attention"] == []
    assert payload["macro"] == {"vix": 0.0, "term": 1.0, "spy_above_200ema": False}

    # Still renders without StrictUndefined explosions.
    rendered = render(MORNING_DIGEST, payload, parse_mode="MarkdownV2")
    assert "Morning Digest" in rendered


def test_morning_digest_ranks_hits_by_score(db: None) -> None:
    _seed_macro()
    _seed_ticker("AAPL")
    _seed_ticker("MSFT")
    _seed_screener_hit(
        symbol="AAPL",
        config_name="Conservative Wheel",
        score=0.81,
        rsi=32.0,
        iv_percentile=67.0,
        close=172.4,
        next_earnings_in_days=38,
    )
    _seed_screener_hit(
        symbol="MSFT",
        config_name="Aggressive Oversold",
        score=0.62,
        rsi=28.0,
        iv_percentile=80.0,
        close=410.0,
        next_earnings_in_days=12,
    )

    with get_session() as session:
        payload = build_morning_digest_payload(session, as_of=AS_OF)

    hits = payload["screener_hits"]
    assert [h["symbol"] for h in hits] == ["AAPL", "MSFT"]
    assert hits[0]["next_earnings_days"] == 38
    assert hits[1]["next_earnings_days"] == 12
    assert hits[0]["rsi"] == "32"
    assert hits[0]["ivp"] == "67"
    assert payload["macro"]["spy_above_200ema"] is True

    rendered = render(MORNING_DIGEST, payload, parse_mode="MarkdownV2")
    assert "AAPL" in rendered and "MSFT" in rendered


def test_morning_digest_includes_earnings_today(db: None) -> None:
    _seed_ticker("NVDA")
    with get_session() as session:
        session.add(
            Earnings(symbol="NVDA", earnings_date=AS_OF, time_of_day="BMO"),
        )

    with get_session() as session:
        payload = build_morning_digest_payload(session, as_of=AS_OF)

    assert payload["earnings_today"] == [{"symbol": "NVDA", "when": "BMO"}]


def test_morning_digest_lists_positions_with_management_triggers(db: None) -> None:
    _seed_ticker("AAPL")
    with get_session() as session:
        # 3.00 credit → 50% profit means option mid = 1.50; we'll set to 1.00.
        sm.open_short_put(
            session,
            sm.OpenShortPutInput(
                symbol="AAPL",
                expiration=AS_OF + timedelta(days=14),
                strike=170.0,
                contracts=1,
                credit=3.00,
                opened_on=AS_OF - timedelta(days=10),
            ),
        )
    with get_session() as session:
        from db.models.positions import Position, PositionSnapshot

        position = session.execute(
            __import__("sqlalchemy").select(Position).where(Position.symbol == "AAPL")
        ).scalar_one()
        session.add(
            PositionSnapshot(
                position_id=position.id,
                snapshot_at=datetime(2026, 5, 4, 22, 0, tzinfo=UTC),
                underlying_price=180.0,
                option_mid=1.00,
                unrealized_pnl=200.0,
                pct_max_profit=0.66,
                delta=-0.10,
                dte=14,
            )
        )

    with get_session() as session:
        payload = build_morning_digest_payload(session, as_of=AS_OF)

    attention = payload["positions_attention"]
    assert len(attention) == 1
    assert attention[0]["symbol"] == "AAPL"
    # 50% profit + 21 DTE both fire.
    assert "50%" in attention[0]["note"] or "profit" in attention[0]["note"]
    assert "DTE" in attention[0]["note"]


def test_evening_digest_includes_pnl_and_tomorrow_earnings(db: None) -> None:
    _seed_macro()
    _seed_ticker("AAPL")
    with get_session() as session:
        session.add(
            Earnings(
                symbol="AAPL",
                earnings_date=AS_OF + timedelta(days=1),
                time_of_day="AMC",
            )
        )
        sm.open_short_put(
            session,
            sm.OpenShortPutInput(
                symbol="AAPL",
                expiration=AS_OF + timedelta(days=30),
                strike=170.0,
                contracts=1,
                credit=3.00,
                opened_on=AS_OF - timedelta(days=2),
            ),
        )
    with get_session() as session:
        from db.models.positions import Position, PositionSnapshot

        position = session.execute(
            __import__("sqlalchemy").select(Position).where(Position.symbol == "AAPL")
        ).scalar_one()
        session.add(
            PositionSnapshot(
                position_id=position.id,
                snapshot_at=datetime(2026, 5, 4, 22, 0, tzinfo=UTC),
                underlying_price=180.0,
                option_mid=2.50,
                unrealized_pnl=50.0,
                pct_max_profit=0.16,
                delta=-0.20,
                dte=30,
            )
        )

    with get_session() as session:
        payload = build_evening_digest_payload(session, as_of=AS_OF)

    assert payload["earnings_tomorrow"] == [{"symbol": "AAPL", "when": "AMC"}]
    assert len(payload["positions"]) == 1
    assert payload["positions"][0] == {
        "position_id": payload["positions"][0]["position_id"],
        "symbol": "AAPL",
        "state": sm.STATE_SHORT_PUT,
        "unrealized_pnl": 50.0,
        "pct_max_profit": 0.16,
        "dte": 30,
    }
    assert isinstance(payload["positions"][0]["position_id"], int)

    rendered = render(EVENING_DIGEST, payload, parse_mode="MarkdownV2")
    assert "Evening Digest" in rendered
    assert "AAPL" in rendered
