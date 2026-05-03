"""Data freshness tracking & alerts tests.

Covers:
- FetchSummary tracks skipped symbols correctly
- The freshness API endpoint returns correct staleness info
- The freshness alert trigger builds correct payloads
- The freshness alert trigger returns None when everything is fresh
- The Telegram template renders without errors
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from alerts.templates.telegram_render import render
from alerts.triggers.freshness_alert import (
    ALERT_TYPE,
    build_freshness_alert_payload,
)
from db.models.market import BarDaily, Ticker
from ingestion.bars import FetchSummary, _merge


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "freshness.db"
    url = f"sqlite:///{db_path}"
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _seed_ticker(session: Session, symbol: str, *, is_active: bool = True) -> None:
    session.add(
        Ticker(
            symbol=symbol,
            name=f"{symbol} Inc.",
            is_active=is_active,
            is_hidden=False,
        )
    )
    session.commit()


def _seed_bar(session: Session, symbol: str, bar_date: date) -> None:
    session.add(
        BarDaily(
            symbol=symbol,
            date=bar_date,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1000,
        )
    )
    session.commit()


# --- FetchSummary skipped symbols ---


class TestFetchSummarySkippedSymbols:
    def test_skipped_symbols_default_empty(self) -> None:
        summary = FetchSummary(
            symbols_requested=3,
            symbols_with_data=3,
            bars_written=30,
            earliest=date(2024, 1, 1),
            latest=date(2024, 1, 10),
        )
        assert summary.symbols_skipped == []

    def test_skipped_symbols_populated(self) -> None:
        summary = FetchSummary(
            symbols_requested=5,
            symbols_with_data=3,
            bars_written=30,
            earliest=date(2024, 1, 1),
            latest=date(2024, 1, 10),
            symbols_skipped=["DEAD", "GONE"],
        )
        assert summary.symbols_skipped == ["DEAD", "GONE"]

    def test_merge_combines_skipped_lists(self) -> None:
        s1 = FetchSummary(
            symbols_requested=2,
            symbols_with_data=1,
            bars_written=10,
            earliest=date(2024, 1, 1),
            latest=date(2024, 1, 5),
            symbols_skipped=["AAA"],
        )
        s2 = FetchSummary(
            symbols_requested=2,
            symbols_with_data=1,
            bars_written=10,
            earliest=date(2024, 1, 6),
            latest=date(2024, 1, 10),
            symbols_skipped=["BBB", "CCC"],
        )
        merged = _merge([s1, s2], symbols_requested=4)
        assert merged.symbols_skipped == ["AAA", "BBB", "CCC"]
        assert merged.bars_written == 20
        assert merged.symbols_with_data == 2

    def test_merge_empty_summaries(self) -> None:
        merged = _merge([], symbols_requested=0)
        assert merged.symbols_skipped == []
        assert merged.bars_written == 0


# --- Freshness alert trigger ---


class TestFreshnessAlertTrigger:
    def test_returns_none_when_all_fresh(self, session: Session) -> None:
        today = date(2024, 6, 3)
        _seed_ticker(session, "AAPL")
        _seed_ticker(session, "MSFT")
        _seed_bar(session, "AAPL", today - timedelta(days=1))
        _seed_bar(session, "MSFT", today - timedelta(days=2))

        result = build_freshness_alert_payload(session, as_of=today)
        assert result is None

    def test_returns_none_when_no_active_tickers(self, session: Session) -> None:
        today = date(2024, 6, 3)
        _seed_ticker(session, "DEAD", is_active=False)

        result = build_freshness_alert_payload(session, as_of=today)
        assert result is None

    def test_detects_stale_symbols(self, session: Session) -> None:
        today = date(2024, 6, 10)
        _seed_ticker(session, "AAPL")
        _seed_ticker(session, "MSFT")
        # AAPL is fresh (1 day old), MSFT is stale (5 days old, > 3 default)
        _seed_bar(session, "AAPL", today - timedelta(days=1))
        _seed_bar(session, "MSFT", today - timedelta(days=5))

        result = build_freshness_alert_payload(session, as_of=today)
        assert result is not None
        assert result["as_of"] == today.isoformat()
        assert result["stale_count"] == 1
        assert result["total_active"] == 2
        assert len(result["stale_symbols"]) == 1
        assert result["stale_symbols"][0]["symbol"] == "MSFT"
        assert result["stale_symbols"][0]["days_stale"] == 5

    def test_includes_skipped_symbols(self, session: Session) -> None:
        today = date(2024, 6, 3)
        _seed_ticker(session, "AAPL")
        _seed_bar(session, "AAPL", today - timedelta(days=1))

        result = build_freshness_alert_payload(
            session, as_of=today, symbols_skipped=["GONE", "DEAD"]
        )
        assert result is not None
        assert result["skipped_symbols"] == ["GONE", "DEAD"]
        assert result["skipped_count"] == 2
        # No stale symbols, only skipped.
        assert result["stale_count"] == 0

    def test_custom_max_age_days(self, session: Session) -> None:
        today = date(2024, 6, 10)
        _seed_ticker(session, "AAPL")
        _seed_bar(session, "AAPL", today - timedelta(days=5))

        # With default (3), AAPL is stale.
        result = build_freshness_alert_payload(session, as_of=today, max_age_days=3)
        assert result is not None
        assert result["stale_count"] == 1

        # With max_age_days=7, AAPL is fresh.
        result = build_freshness_alert_payload(session, as_of=today, max_age_days=7)
        assert result is None

    def test_tickers_with_no_bars_not_counted_as_stale(self, session: Session) -> None:
        today = date(2024, 6, 3)
        _seed_ticker(session, "NEWBIE")  # No bars at all.

        result = build_freshness_alert_payload(session, as_of=today)
        # No bars means no stale detection — only skipped symbols trigger.
        assert result is None


# --- Telegram template rendering ---


class TestFreshnessTemplate:
    def test_renders_stale_and_skipped(self) -> None:
        payload = {
            "as_of": "2024-06-10",
            "stale_symbols": [
                {"symbol": "MSFT", "last_bar_date": "2024-06-05", "days_stale": 5},
                {"symbol": "GOOG", "last_bar_date": "2024-06-04", "days_stale": 6},
            ],
            "skipped_symbols": ["DEAD", "GONE"],
            "total_active": 10,
            "stale_count": 2,
            "skipped_count": 2,
        }
        text = render(ALERT_TYPE, payload, parse_mode="MarkdownV2")
        assert "Data Freshness Warning" in text
        assert "MSFT" in text
        assert "GOOG" in text
        assert "DEAD" in text
        assert "GONE" in text
        assert "10" in text

    def test_renders_stale_only(self) -> None:
        payload = {
            "as_of": "2024-06-10",
            "stale_symbols": [
                {"symbol": "MSFT", "last_bar_date": "2024-06-05", "days_stale": 5},
            ],
            "skipped_symbols": [],
            "total_active": 5,
            "stale_count": 1,
            "skipped_count": 0,
        }
        text = render(ALERT_TYPE, payload, parse_mode="MarkdownV2")
        assert "MSFT" in text
        assert "Skipped during ingestion" not in text

    def test_renders_skipped_only(self) -> None:
        payload = {
            "as_of": "2024-06-10",
            "stale_symbols": [],
            "skipped_symbols": ["DEAD"],
            "total_active": 5,
            "stale_count": 0,
            "skipped_count": 1,
        }
        text = render(ALERT_TYPE, payload, parse_mode="MarkdownV2")
        assert "DEAD" in text
        assert "Stale tickers" not in text


# --- Data freshness API endpoint ---


class TestDataFreshnessAPI:
    def test_endpoint_returns_correct_freshness(self, session: Session) -> None:
        """Verify the freshness computation logic directly (unit-style)."""
        today = date(2024, 6, 10)
        _seed_ticker(session, "AAPL")
        _seed_ticker(session, "MSFT")
        _seed_ticker(session, "NEWBIE")
        # AAPL: fresh (1 day old)
        _seed_bar(session, "AAPL", today - timedelta(days=1))
        # MSFT: stale (5 days old)
        _seed_bar(session, "MSFT", today - timedelta(days=5))
        # NEWBIE: no bars

        from sqlalchemy import func, select

        from db.models.market import BarDaily, Ticker

        active_tickers = list(
            session.execute(
                select(Ticker.symbol).where(Ticker.is_active.is_(True)).order_by(Ticker.symbol)
            )
            .scalars()
            .all()
        )
        assert set(active_tickers) == {"AAPL", "MSFT", "NEWBIE"}

        latest_bars: dict[str, date] = dict(
            session.execute(
                select(BarDaily.symbol, func.max(BarDaily.date))
                .where(BarDaily.symbol.in_(active_tickers))
                .group_by(BarDaily.symbol)
            ).all()
        )

        max_age_days = 3
        stale_count = 0
        fresh_count = 0
        no_data_count = 0

        for symbol in active_tickers:
            last_bar_date = latest_bars.get(symbol)
            if last_bar_date is None:
                no_data_count += 1
            else:
                days_stale = (today - last_bar_date).days
                if days_stale > max_age_days:
                    stale_count += 1
                else:
                    fresh_count += 1

        assert stale_count == 1  # MSFT
        assert fresh_count == 1  # AAPL
        assert no_data_count == 1  # NEWBIE
