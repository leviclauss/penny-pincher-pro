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

    def send(
        self,
        alert_type: str,
        payload: dict[str, Any],
        *,
        alert_id: int | None = None,
    ) -> ChannelResult:
        self.calls.append((alert_type, payload))
        self.last_alert_id = alert_id
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


def test_dispatch_fans_out_to_all_enabled_channels(db: None) -> None:
    """Each channel listed in preferences receives the same payload + one alerts row."""
    from db import get_session

    with get_session() as session:
        session.add(
            AlertPreference(
                alert_type="iv_spike",
                channels=["telegram", "email", "ntfy"],
                enabled=True,
            )
        )

    fakes = {
        "telegram": FakeChannel("telegram"),
        "email": FakeChannel("email"),
        "ntfy": FakeChannel("ntfy"),
    }
    registry: dict[str, Channel] = dict(fakes)

    payload = {"symbol": "AAPL", "as_of": "2026-05-04"}
    result = dispatch("iv_spike", payload, registry=registry)

    assert result.channels_attempted == ["telegram", "email", "ntfy"]
    assert result.channels_sent == ["telegram", "email", "ntfy"]
    for fake in fakes.values():
        assert fake.calls == [("iv_spike", payload)]

    # One alert row, not three.
    with get_session() as session:
        rows = session.execute(select(Alert).where(Alert.id == result.alert_id)).scalars().all()
        assert len(rows) == 1
        channels_sent = rows[0].channels_sent
        assert channels_sent is not None
        assert "telegram" in channels_sent
        assert "email" in channels_sent
        assert "ntfy" in channels_sent


def test_dispatch_skips_when_per_type_snoozed(db: None) -> None:
    from datetime import UTC, datetime, timedelta

    from db import get_session

    with get_session() as session:
        session.add(
            AlertPreference(
                alert_type="setup",
                channels=["telegram"],
                enabled=True,
                snooze_until=datetime.now(UTC) + timedelta(hours=1),
            )
        )

    fake = FakeChannel("telegram")
    result = dispatch("setup", {"symbol": "AAPL"}, registry={"telegram": fake})
    assert result.skipped_reason == "snoozed"
    assert fake.calls == []
    assert result.alert_id is None


def test_dispatch_continues_after_one_channel_failure(db: None) -> None:
    """Sibling channels still deliver when one fails; alert row is still written."""
    from db import get_session

    with get_session() as session:
        session.add(
            AlertPreference(
                alert_type="setup_triggered",
                channels=["telegram", "email", "ntfy"],
                enabled=True,
            )
        )

    fakes = {
        "telegram": FakeChannel("telegram", delivered=True),
        "email": FakeChannel("email", delivered=False),
        "ntfy": FakeChannel("ntfy", delivered=True),
    }
    result = dispatch(
        "setup_triggered",
        {"symbol": "MSFT"},
        registry=dict(fakes),
    )

    assert result.channels_attempted == ["telegram", "email", "ntfy"]
    assert result.channels_sent == ["telegram", "ntfy"]
    assert result.alert_id is not None
    # All three channels were tried even though email failed.
    for fake in fakes.values():
        assert len(fake.calls) == 1


def test_dispatch_swallows_channel_exception(db: None) -> None:
    """An exception inside ``send`` is logged and skipped, not propagated."""
    from db import get_session

    with get_session() as session:
        session.add(
            AlertPreference(
                alert_type="iv_spike",
                channels=["explody", "ntfy"],
                enabled=True,
            )
        )

    class ExplodingChannel:
        id = "explody"

        def send(self, alert_type: str, payload: dict[str, Any]) -> ChannelResult:
            raise RuntimeError("kaboom")

    ntfy = FakeChannel("ntfy")
    registry: dict[str, Channel] = {"explody": ExplodingChannel(), "ntfy": ntfy}
    result = dispatch("iv_spike", {"symbol": "AAPL"}, registry=registry)

    assert result.channels_attempted == ["explody", "ntfy"]
    assert result.channels_sent == ["ntfy"]
    assert len(ntfy.calls) == 1
    assert result.alert_id is not None


def test_dispatch_skips_when_global_snoozed(db: None) -> None:
    """`/snooze 30m` writes the __global__ row; every alert type is muted."""
    from datetime import UTC, datetime, timedelta

    from alerts.dispatcher import GLOBAL_SNOOZE_KEY
    from db import get_session

    with get_session() as session:
        session.add(
            AlertPreference(
                alert_type=GLOBAL_SNOOZE_KEY,
                channels=[],
                enabled=True,
                snooze_until=datetime.now(UTC) + timedelta(hours=1),
            )
        )

    fake = FakeChannel("telegram")
    result = dispatch("morning_digest", {"symbol": "AAPL"}, registry={"telegram": fake})
    assert result.skipped_reason == "snoozed"
    assert fake.calls == []


def test_dispatch_passes_alert_id_to_channel(db: None) -> None:
    """Channels see the freshly-persisted row id so they can attach callbacks."""
    fake = FakeChannel("telegram")
    result = dispatch("morning_digest", {"symbol": "AAPL"}, registry={"telegram": fake})
    assert result.alert_id is not None
    assert fake.last_alert_id == result.alert_id
