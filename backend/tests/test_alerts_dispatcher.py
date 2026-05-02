"""Tests for the alert dispatcher.

Covers preference loading (default + DB override), quiet-hours skipping,
unknown channels, and persistence of the alerts row regardless of delivery
outcome.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import time
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import select

from alembic import command
from alerts.channels.base import Channel, ChannelResult
from alerts.dispatcher import dispatch
from db.models.alerts import Alert, AlertPreference


class FakeChannel:
    def __init__(self, channel_id: str, *, delivered: bool = True, msg_id: str = "1") -> None:
        self.id = channel_id
        self._delivered = delivered
        self._msg_id = msg_id
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def send(self, alert_type: str, payload: dict[str, Any]) -> ChannelResult:
        self.calls.append((alert_type, payload))
        if self._delivered:
            return ChannelResult(True, self._msg_id, None)
        return ChannelResult(False, None, "boom")


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_path = tmp_path / "dispatch.db"
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


def test_dispatch_uses_default_preference_when_none(db: None) -> None:
    fake: dict[str, Channel] = {"telegram": FakeChannel("telegram")}
    payload = {"symbol": "AAPL", "as_of": "2026-05-02"}
    result = dispatch("morning_digest", payload, registry=fake)

    assert result.channels_attempted == ["telegram"]
    assert result.channels_sent == ["telegram"]
    assert result.alert_id is not None

    from db import get_session

    with get_session() as session:
        row = session.execute(select(Alert).where(Alert.id == result.alert_id)).scalar_one()
        assert row.alert_type == "morning_digest"
        assert row.symbol == "AAPL"
        assert row.channels_sent is not None
        assert "telegram" in row.channels_sent


def test_dispatch_loads_preference_from_db(db: None) -> None:
    from db import get_session

    with get_session() as session:
        session.add(
            AlertPreference(
                alert_type="setup",
                channels=["telegram", "push"],
                enabled=True,
            )
        )

    fake_telegram = FakeChannel("telegram")
    fake_push = FakeChannel("push", delivered=False)
    registry: dict[str, Channel] = {"telegram": fake_telegram, "push": fake_push}

    result = dispatch("setup", {"symbol": "MSFT"}, registry=registry)

    assert result.channels_attempted == ["telegram", "push"]
    assert result.channels_sent == ["telegram"]
    assert len(fake_telegram.calls) == 1
    assert len(fake_push.calls) == 1


def test_dispatch_persists_row_when_no_channel_delivers(db: None) -> None:
    fake: dict[str, Channel] = {"telegram": FakeChannel("telegram", delivered=False)}
    result = dispatch("morning_digest", {"symbol": "MSFT"}, registry=fake)

    assert result.channels_sent == []
    assert result.alert_id is not None

    from db import get_session

    with get_session() as session:
        row = session.execute(select(Alert).where(Alert.id == result.alert_id)).scalar_one()
        assert row.channels_sent is None


def test_dispatch_skips_when_disabled(db: None) -> None:
    from db import get_session

    with get_session() as session:
        session.add(AlertPreference(alert_type="setup", channels=["telegram"], enabled=False))

    fake = FakeChannel("telegram")
    result = dispatch("setup", {"symbol": "AAPL"}, registry={"telegram": fake})
    assert result.skipped_reason == "disabled"
    assert fake.calls == []
    assert result.alert_id is None


def test_dispatch_skips_quiet_hours(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from db import get_session

    with get_session() as session:
        session.add(
            AlertPreference(
                alert_type="setup",
                channels=["telegram"],
                enabled=True,
                quiet_hours_start=time(0, 0),
                quiet_hours_end=time(23, 59),
            )
        )

    fake = FakeChannel("telegram")
    result = dispatch("setup", {"symbol": "AAPL"}, registry={"telegram": fake})
    assert result.skipped_reason == "quiet_hours"
    assert fake.calls == []


def test_dispatch_warns_on_unknown_channel(db: None) -> None:
    from db import get_session

    with get_session() as session:
        session.add(AlertPreference(alert_type="setup", channels=["sms"], enabled=True))

    result = dispatch("setup", {"symbol": "AAPL"}, registry={})
    assert result.channels_attempted == ["sms"]
    assert result.channels_sent == []
    assert result.alert_id is not None
