"""Tests for the inbound Telegram bot (phase 3-4).

Covers:
- ``getUpdates`` payload parsing + offset persistence in ``bot_state``.
- Allow-list enforcement (foreign chats are dropped, not handled).
- ``/status`` reply assembly (last bar, recent jobs, unacked count).
- ``/snooze 30m`` writes the global snooze row; ``/snooze off`` clears it;
  the dispatcher then refuses to deliver while the cutoff is in the future.
- ``ack:<id>`` callback toggles ``user_acked`` and edits the message.

All HTTP traffic goes through ``respx`` — no real network.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from alembic.config import Config
from sqlalchemy import select

from alembic import command
from alerts.channels.base import Channel, ChannelResult
from alerts.channels.telegram_inbound import (
    OFFSET_KEY,
    TelegramInboundBot,
    parse_snooze_duration,
)
from alerts.dispatcher import GLOBAL_SNOOZE_KEY, dispatch
from db import get_session
from db.models.alerts import Alert, AlertPreference
from db.models.market import BarDaily, Ticker
from db.models.system import BotState, JobRun

ALLOWED_CHAT_ID = "42"
TOKEN = "tkn"
BASE = f"https://api.telegram.org/bot{TOKEN}"


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_path = tmp_path / "inbound.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", ALLOWED_CHAT_ID)
    monkeypatch.setenv("TELEGRAM_INBOUND_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_INBOUND_LONG_POLL_S", "1")

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


def _make_bot() -> TelegramInboundBot:
    return TelegramInboundBot(
        token=TOKEN,
        allowed_chat_id=ALLOWED_CHAT_ID,
        long_poll_s=1,
        idle_sleep_s=0.01,
        max_failures=2,
    )


def _message_update(
    text: str,
    *,
    update_id: int = 1,
    chat_id: str = ALLOWED_CHAT_ID,
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 100 + update_id,
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


def _callback_update(
    data: str, *, update_id: int = 1, chat_id: str = ALLOWED_CHAT_ID, message_id: int = 555
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb-{update_id}",
            "data": data,
            "message": {
                "message_id": message_id,
                "chat": {"id": chat_id, "type": "private"},
            },
        },
    }


# ----------------------------------------------------------------------
# parse_snooze_duration
# ----------------------------------------------------------------------
def test_parse_snooze_duration_accepts_minutes_hours_days() -> None:
    assert parse_snooze_duration("30m") == timedelta(minutes=30)
    assert parse_snooze_duration("2H") == timedelta(hours=2)
    assert parse_snooze_duration("  1d ") == timedelta(days=1)


def test_parse_snooze_duration_rejects_garbage() -> None:
    assert parse_snooze_duration("forever") is None
    assert parse_snooze_duration("0m") is None
    assert parse_snooze_duration("5x") is None
    assert parse_snooze_duration("") is None


# ----------------------------------------------------------------------
# Polling cycle + offset persistence
# ----------------------------------------------------------------------
@respx.mock
def test_poll_once_persists_next_offset(db: None) -> None:
    respx.get(f"{BASE}/getUpdates").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    _message_update("/help", update_id=10),
                    _message_update("/help", update_id=11),
                ],
            },
        )
    )
    respx.post(f"{BASE}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    )

    bot = _make_bot()
    handled = bot.poll_once()
    assert handled == 2

    with get_session() as session:
        row = session.execute(select(BotState).where(BotState.key == OFFSET_KEY)).scalar_one()
        assert row.value == "12"


@respx.mock
def test_poll_once_uses_stored_offset_on_followup(db: None) -> None:
    with get_session() as session:
        session.add(
            BotState(key=OFFSET_KEY, value="42", updated_at=datetime(2026, 5, 1, tzinfo=UTC))
        )

    route = respx.get(f"{BASE}/getUpdates").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": []})
    )
    bot = _make_bot()
    bot.poll_once()

    request = route.calls.last.request
    assert request.url.params["offset"] == "42"


# ----------------------------------------------------------------------
# Allow-list
# ----------------------------------------------------------------------
@respx.mock
def test_unauthorized_chat_is_rejected(db: None) -> None:
    respx.get(f"{BASE}/getUpdates").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": [_message_update("/status", chat_id="999", update_id=1)],
            },
        )
    )
    reply = respx.post(f"{BASE}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    )

    bot = _make_bot()
    bot.poll_once()

    # We *do* reply "unauthorized" so the user knows the bot saw them, but
    # no command logic ran (no /status reply, etc.). Check the reply chat id.
    body = json.loads(reply.calls.last.request.content)
    assert body["chat_id"] == "999"
    assert body["text"] == "unauthorized"


# ----------------------------------------------------------------------
# /status command
# ----------------------------------------------------------------------
@respx.mock
def test_status_command_includes_bar_jobs_unacked(db: None) -> None:
    with get_session() as session:
        session.add(Ticker(symbol="AAPL", is_active=True))
        session.add(
            BarDaily(
                symbol="AAPL",
                date=date(2026, 5, 2),
                open=1,
                high=1,
                low=1,
                close=1,
                volume=1,
            )
        )
        session.add(
            JobRun(
                job_name="evening_pipeline",
                started_at=datetime(2026, 5, 2, 1, 0, tzinfo=UTC),
                ended_at=datetime(2026, 5, 2, 1, 5, tzinfo=UTC),
                status="success",
            )
        )
        session.add(
            Alert(
                alert_type="setup_triggered",
                symbol="AAPL",
                payload_json={"symbol": "AAPL"},
                triggered_at=datetime.now(UTC) - timedelta(hours=1),
                user_acked=False,
            )
        )
        session.add(
            Alert(
                alert_type="setup_triggered",
                symbol="MSFT",
                payload_json={"symbol": "MSFT"},
                triggered_at=datetime.now(UTC) - timedelta(hours=2),
                user_acked=True,  # acked → excluded
            )
        )

    respx.get(f"{BASE}/getUpdates").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": [_message_update("/status")]})
    )
    reply = respx.post(f"{BASE}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})
    )

    bot = _make_bot()
    handled = bot.poll_once()
    assert handled == 1

    body = json.loads(reply.calls.last.request.content)
    assert body["chat_id"] == ALLOWED_CHAT_ID
    text = body["text"]
    assert "Status" in text
    # MarkdownV2 escapes "-" → "\-" and "_" → "\_".
    assert "2026\\-05\\-02" in text
    assert "evening\\_pipeline" in text
    assert "Unacked alerts" in text
    assert "1" in text


# ----------------------------------------------------------------------
# /snooze command
# ----------------------------------------------------------------------
@respx.mock
def test_snooze_writes_global_row_and_dispatcher_skips(db: None) -> None:
    respx.get(f"{BASE}/getUpdates").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "result": [_message_update("/snooze 1h")]}
        )
    )
    respx.post(f"{BASE}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    )

    bot = _make_bot()
    bot.poll_once()

    with get_session() as session:
        row = session.execute(
            select(AlertPreference).where(AlertPreference.alert_type == GLOBAL_SNOOZE_KEY)
        ).scalar_one()
        assert row.snooze_until is not None
        # SQLite drops tz info on read — re-attach UTC for the comparison.
        snooze_at = row.snooze_until
        if snooze_at.tzinfo is None:
            snooze_at = snooze_at.replace(tzinfo=UTC)
        assert snooze_at > datetime.now(UTC)

    # Verify the dispatcher honors the global snooze.
    class _NoopChannel:
        id = "telegram"

        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def send(
            self,
            alert_type: str,
            payload: dict[str, Any],
            *,
            alert_id: int | None = None,
        ) -> ChannelResult:
            self.calls.append((alert_type, payload))
            return ChannelResult(True, "1", None)

    fake = _NoopChannel()
    registry: dict[str, Channel] = {"telegram": fake}
    result = dispatch("morning_digest", {"symbol": "AAPL"}, registry=registry)
    assert result.skipped_reason == "snoozed"
    assert fake.calls == []


@respx.mock
def test_snooze_off_clears_snooze(db: None) -> None:
    with get_session() as session:
        session.add(
            AlertPreference(
                alert_type=GLOBAL_SNOOZE_KEY,
                channels=[],
                enabled=True,
                snooze_until=datetime.now(UTC) + timedelta(hours=2),
            )
        )

    respx.get(f"{BASE}/getUpdates").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "result": [_message_update("/snooze off")]}
        )
    )
    respx.post(f"{BASE}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    )

    bot = _make_bot()
    bot.poll_once()

    with get_session() as session:
        row = session.execute(
            select(AlertPreference).where(AlertPreference.alert_type == GLOBAL_SNOOZE_KEY)
        ).scalar_one()
        assert row.snooze_until is None


@respx.mock
def test_snooze_bad_input_reports_usage(db: None) -> None:
    respx.get(f"{BASE}/getUpdates").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "result": [_message_update("/snooze forever")]}
        )
    )
    reply = respx.post(f"{BASE}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    )

    bot = _make_bot()
    bot.poll_once()

    text = json.loads(reply.calls.last.request.content)["text"]
    assert "parse" in text.lower() or "snooze" in text.lower()

    with get_session() as session:
        row = session.execute(
            select(AlertPreference).where(AlertPreference.alert_type == GLOBAL_SNOOZE_KEY)
        ).scalar_one_or_none()
        # Bad input must not write a snooze.
        assert row is None or row.snooze_until is None


# ----------------------------------------------------------------------
# Inline ack callback
# ----------------------------------------------------------------------
@respx.mock
def test_ack_callback_marks_alert_acked_and_edits_message(db: None) -> None:
    with get_session() as session:
        alert = Alert(
            alert_type="setup_triggered",
            symbol="AAPL",
            payload_json={"symbol": "AAPL"},
            triggered_at=datetime.now(UTC),
            user_acked=False,
        )
        session.add(alert)
        session.flush()
        alert_id = alert.id

    respx.get(f"{BASE}/getUpdates").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": [_callback_update(f"ack:{alert_id}")],
            },
        )
    )
    answer = respx.post(f"{BASE}/answerCallbackQuery").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )
    edit = respx.post(f"{BASE}/editMessageReplyMarkup").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )

    bot = _make_bot()
    handled = bot.poll_once()
    assert handled == 1

    with get_session() as session:
        row = session.execute(select(Alert).where(Alert.id == alert_id)).scalar_one()
        assert row.user_acked is True

    assert answer.called
    assert edit.called

    edit_body = json.loads(edit.calls.last.request.content)
    assert edit_body["message_id"] == 555
    assert edit_body["reply_markup"]["inline_keyboard"][0][0]["text"].startswith("✓")


@respx.mock
def test_callback_from_unauthorized_chat_is_rejected(db: None) -> None:
    respx.get(f"{BASE}/getUpdates").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": [_callback_update("ack:1", chat_id="999")],
            },
        )
    )
    answer = respx.post(f"{BASE}/answerCallbackQuery").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )
    # If we touched editMessageReplyMarkup, the test should fail — that
    # would mean the auth check let an unauthorized callback through.
    edit = respx.post(f"{BASE}/editMessageReplyMarkup").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )

    bot = _make_bot()
    bot.poll_once()

    assert answer.called
    assert not edit.called


@respx.mock
def test_ack_missing_alert_returns_friendly_text(db: None) -> None:
    respx.get(f"{BASE}/getUpdates").mock(
        return_value=httpx.Response(
            200,
            json={"ok": True, "result": [_callback_update("ack:9999")]},
        )
    )
    answer = respx.post(f"{BASE}/answerCallbackQuery").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )

    bot = _make_bot()
    bot.poll_once()

    body = json.loads(answer.calls.last.request.content)
    assert "not found" in body["text"].lower()
