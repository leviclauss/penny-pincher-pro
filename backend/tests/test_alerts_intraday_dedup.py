"""Per-symbol-per-day + morning-digest dedup helpers used by the intraday pulse.

The helpers are pure SQL — these tests pin down exactly when they say "yes,
already dispatched" so the intraday job can rely on them instead of
re-implementing the same predicate per trigger family.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from alerts.triggers._dedup import (
    already_dispatched_for_symbol_on,
    symbol_in_morning_digest,
)
from db import get_session
from db.models.alerts import Alert

AS_OF = date(2026, 5, 4)


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_path = tmp_path / "intraday_dedup.db"
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


def test_per_symbol_returns_false_when_no_rows(db: None) -> None:
    with get_session() as session:
        assert (
            already_dispatched_for_symbol_on(session, "setup_triggered", as_of=AS_OF, symbol="AAPL")
            is False
        )


def test_per_symbol_matches_type_symbol_and_as_of(db: None) -> None:
    with get_session() as session:
        session.add(
            Alert(
                alert_type="setup_triggered",
                symbol="AAPL",
                payload_json={"as_of": AS_OF.isoformat(), "symbol": "AAPL"},
            )
        )

    with get_session() as session:
        assert already_dispatched_for_symbol_on(
            session, "setup_triggered", as_of=AS_OF, symbol="AAPL"
        )
        # Different alert_type → no match.
        assert not already_dispatched_for_symbol_on(session, "iv_spike", as_of=AS_OF, symbol="AAPL")
        # Different symbol → no match.
        assert not already_dispatched_for_symbol_on(
            session, "setup_triggered", as_of=AS_OF, symbol="MSFT"
        )
        # Different day → no match (the next trading day re-arms the alert).
        assert not already_dispatched_for_symbol_on(
            session, "setup_triggered", as_of=date(2026, 5, 5), symbol="AAPL"
        )


def test_morning_digest_membership_when_no_digest(db: None) -> None:
    with get_session() as session:
        assert symbol_in_morning_digest(session, as_of=AS_OF, symbol="AAPL") is False


def test_morning_digest_membership_finds_symbol_in_screener_hits(db: None) -> None:
    with get_session() as session:
        session.add(
            Alert(
                alert_type="morning_digest",
                payload_json={
                    "as_of": AS_OF.isoformat(),
                    "screener_hits": [
                        {"symbol": "AAPL", "config": "Conservative Wheel"},
                        {"symbol": "MSFT", "config": "Aggressive Oversold"},
                    ],
                },
            )
        )

    with get_session() as session:
        assert symbol_in_morning_digest(session, as_of=AS_OF, symbol="AAPL")
        assert symbol_in_morning_digest(session, as_of=AS_OF, symbol="MSFT")
        # Symbol not in this morning's hits → not suppressed.
        assert not symbol_in_morning_digest(session, as_of=AS_OF, symbol="GOOG")
        # Yesterday's digest doesn't suppress today's intraday.
        assert not symbol_in_morning_digest(session, as_of=date(2026, 5, 5), symbol="AAPL")


def test_morning_digest_membership_handles_empty_hits(db: None) -> None:
    with get_session() as session:
        session.add(
            Alert(
                alert_type="morning_digest",
                payload_json={"as_of": AS_OF.isoformat(), "screener_hits": []},
            )
        )

    with get_session() as session:
        assert not symbol_in_morning_digest(session, as_of=AS_OF, symbol="AAPL")
