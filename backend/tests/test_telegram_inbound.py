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
    _parse_int_arg,
    _parse_symbol_arg,
    parse_snooze_duration,
)
from alerts.dispatcher import GLOBAL_SNOOZE_KEY, dispatch
from db import get_session
from db.models.alerts import Alert, AlertPreference
from db.models.market import BarDaily, Earnings, IndicatorDaily, MacroDaily, Ticker
from db.models.positions import Position, PositionLeg, PositionSnapshot
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


# ----------------------------------------------------------------------
# Arg-parsing helpers
# ----------------------------------------------------------------------
def test_parse_int_arg_clamps_and_falls_back() -> None:
    assert _parse_int_arg("", default=7, max_value=30) == 7
    assert _parse_int_arg("abc", default=7, max_value=30) == 7
    assert _parse_int_arg("0", default=7, max_value=30) == 7
    assert _parse_int_arg("3", default=7, max_value=30) == 3
    assert _parse_int_arg("999", default=7, max_value=30) == 30


def test_parse_symbol_arg_normalises_and_validates() -> None:
    assert _parse_symbol_arg("aapl") == "AAPL"
    assert _parse_symbol_arg("  msft  ") == "MSFT"
    assert _parse_symbol_arg("BRK.B") == "BRK.B"
    assert _parse_symbol_arg("") is None
    assert _parse_symbol_arg("123") is None
    assert _parse_symbol_arg("toolongsymbolname1") is None


# ----------------------------------------------------------------------
# /macro command
# ----------------------------------------------------------------------
def _mock_send_message() -> respx.Route:
    return respx.post(f"{BASE}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    )


def _mock_get_updates(updates: list[dict[str, Any]]) -> respx.Route:
    return respx.get(f"{BASE}/getUpdates").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": updates})
    )


@respx.mock
def test_macro_command_renders_latest_snapshot(db: None) -> None:
    with get_session() as session:
        session.add(
            MacroDaily(
                date=date(2026, 5, 2),
                vix_close=14.25,
                vix_9d=13.10,
                vix_term_structure=0.92,
                spy_close=520.50,
                spy_ema_200=480.10,
                spy_above_200ema=True,
            )
        )

    _mock_get_updates([_message_update("/macro")])
    reply = _mock_send_message()

    bot = _make_bot()
    handled = bot.poll_once()
    assert handled == 1

    text = json.loads(reply.calls.last.request.content)["text"]
    assert "Macro" in text
    assert "14\\.25" in text
    assert "backwardation" in text
    assert "above 200EMA" in text


@respx.mock
def test_macro_command_handles_empty(db: None) -> None:
    _mock_get_updates([_message_update("/macro")])
    reply = _mock_send_message()

    bot = _make_bot()
    bot.poll_once()

    text = json.loads(reply.calls.last.request.content)["text"]
    assert "No macro snapshot" in text


# ----------------------------------------------------------------------
# /earnings command
# ----------------------------------------------------------------------
@respx.mock
def test_earnings_command_lists_upcoming(db: None) -> None:
    today = date.today()
    with get_session() as session:
        session.add(Ticker(symbol="AAPL", is_active=True))
        session.add(Ticker(symbol="MSFT", is_active=True))
        session.add(
            Earnings(
                symbol="AAPL",
                earnings_date=today + timedelta(days=2),
                time_of_day="AMC",
            )
        )
        session.add(
            Earnings(
                symbol="MSFT",
                earnings_date=today + timedelta(days=5),
                time_of_day="BMO",
            )
        )
        # Outside window
        session.add(Earnings(symbol="AAPL", earnings_date=today + timedelta(days=20)))

    _mock_get_updates([_message_update("/earnings")])
    reply = _mock_send_message()

    bot = _make_bot()
    bot.poll_once()

    text = json.loads(reply.calls.last.request.content)["text"]
    assert "Earnings" in text
    assert "AAPL" in text
    assert "MSFT" in text
    assert "AMC" in text
    assert "BMO" in text


@respx.mock
def test_earnings_command_respects_arg(db: None) -> None:
    today = date.today()
    with get_session() as session:
        session.add(Ticker(symbol="AAPL", is_active=True))
        session.add(Earnings(symbol="AAPL", earnings_date=today + timedelta(days=14)))

    # 7-day default would not include the day-14 row.
    _mock_get_updates([_message_update("/earnings 21")])
    reply = _mock_send_message()
    bot = _make_bot()
    bot.poll_once()
    text = json.loads(reply.calls.last.request.content)["text"]
    assert "AAPL" in text
    assert "21d" in text


@respx.mock
def test_earnings_command_empty(db: None) -> None:
    _mock_get_updates([_message_update("/earnings")])
    reply = _mock_send_message()
    bot = _make_bot()
    bot.poll_once()
    text = json.loads(reply.calls.last.request.content)["text"]
    assert "No upcoming earnings" in text


# ----------------------------------------------------------------------
# /positions command
# ----------------------------------------------------------------------
@respx.mock
def test_positions_command_renders_open_only(db: None) -> None:
    with get_session() as session:
        open_pos = Position(
            symbol="AAPL",
            state="short_put",
            opened_at=datetime.now(UTC) - timedelta(days=3),
        )
        closed_pos = Position(
            symbol="MSFT",
            state="closed",
            opened_at=datetime.now(UTC) - timedelta(days=10),
            closed_at=datetime.now(UTC) - timedelta(days=1),
        )
        session.add(open_pos)
        session.add(closed_pos)
        session.flush()
        session.add(
            PositionLeg(
                position_id=open_pos.id,
                leg_type="short_put",
                symbol="AAPL",
                expiration=date(2026, 6, 19),
                strike=170.0,
                contracts=1,
                entry_price=2.50,
                outcome="open",
            )
        )
        session.add(
            PositionSnapshot(
                position_id=open_pos.id,
                snapshot_at=datetime.now(UTC),
                option_mid=1.20,
                unrealized_pnl=130.0,
                dte=47,
            )
        )

    _mock_get_updates([_message_update("/positions")])
    reply = _mock_send_message()
    bot = _make_bot()
    bot.poll_once()

    text = json.loads(reply.calls.last.request.content)["text"]
    assert "Open positions" in text
    assert "AAPL" in text
    assert "MSFT" not in text  # closed
    assert "47" in text  # DTE


@respx.mock
def test_positions_command_empty(db: None) -> None:
    _mock_get_updates([_message_update("/positions")])
    reply = _mock_send_message()
    bot = _make_bot()
    bot.poll_once()
    text = json.loads(reply.calls.last.request.content)["text"]
    assert "No open positions" in text


# ----------------------------------------------------------------------
# /ticker command
# ----------------------------------------------------------------------
@respx.mock
def test_ticker_command_renders_summary(db: None) -> None:
    today = date(2026, 5, 2)
    with get_session() as session:
        session.add(Ticker(symbol="AAPL", name="Apple Inc.", is_active=True))
        session.add(
            BarDaily(symbol="AAPL", date=today, open=170, high=172, low=168, close=170.5, volume=1)
        )
        session.add(
            BarDaily(
                symbol="AAPL",
                date=today - timedelta(days=1),
                open=168,
                high=170,
                low=167,
                close=168.0,
                volume=1,
            )
        )
        session.add(
            IndicatorDaily(
                symbol="AAPL",
                date=today,
                ema_200=160.0,
                rsi_14=55.5,
                iv_atm=0.28,
                iv_rank=42.0,
                iv_percentile=51.0,
            )
        )
        session.add(Earnings(symbol="AAPL", earnings_date=date.today() + timedelta(days=10)))

    _mock_get_updates([_message_update("/ticker AAPL")])
    reply = _mock_send_message()
    bot = _make_bot()
    bot.poll_once()

    text = json.loads(reply.calls.last.request.content)["text"]
    assert "AAPL" in text
    assert "Apple Inc" in text
    assert "170\\.50" in text  # close
    assert "above" in text  # EMA200 side
    assert "55\\.5" in text  # RSI
    assert "28\\.0%" in text  # IV ATM


@respx.mock
def test_ticker_command_unknown_symbol(db: None) -> None:
    _mock_get_updates([_message_update("/ticker BOGUS")])
    reply = _mock_send_message()
    bot = _make_bot()
    bot.poll_once()
    text = json.loads(reply.calls.last.request.content)["text"]
    assert "Unknown symbol" in text


@respx.mock
def test_ticker_command_missing_arg(db: None) -> None:
    _mock_get_updates([_message_update("/ticker")])
    reply = _mock_send_message()
    bot = _make_bot()
    bot.poll_once()
    text = json.loads(reply.calls.last.request.content)["text"]
    assert "Usage" in text


# ----------------------------------------------------------------------
# /alerts command
# ----------------------------------------------------------------------
@respx.mock
def test_alerts_command_renders_recent(db: None) -> None:
    with get_session() as session:
        session.add(
            Alert(
                alert_type="setup_triggered",
                symbol="AAPL",
                payload_json={"symbol": "AAPL"},
                triggered_at=datetime(2026, 5, 2, 13, 30, tzinfo=UTC),
                user_acked=True,
            )
        )
        session.add(
            Alert(
                alert_type="iv_spike",
                symbol="MSFT",
                payload_json={"symbol": "MSFT"},
                triggered_at=datetime(2026, 5, 2, 14, 45, tzinfo=UTC),
                user_acked=False,
            )
        )

    _mock_get_updates([_message_update("/alerts")])
    reply = _mock_send_message()
    bot = _make_bot()
    bot.poll_once()

    text = json.loads(reply.calls.last.request.content)["text"]
    assert "Recent alerts" in text
    assert "AAPL" in text
    assert "MSFT" in text
    assert "iv\\_spike" in text
    # Newest first → MSFT line precedes AAPL line.
    assert text.index("MSFT") < text.index("AAPL")


@respx.mock
def test_alerts_command_respects_limit(db: None) -> None:
    with get_session() as session:
        for i in range(5):
            session.add(
                Alert(
                    alert_type="setup_triggered",
                    symbol=f"SYM{i}",
                    payload_json={"i": i},
                    triggered_at=datetime(2026, 5, 2, 10, i, tzinfo=UTC),
                )
            )

    _mock_get_updates([_message_update("/alerts 2")])
    reply = _mock_send_message()
    bot = _make_bot()
    bot.poll_once()

    text = json.loads(reply.calls.last.request.content)["text"]
    # Newest two: SYM4 + SYM3.
    assert "SYM4" in text
    assert "SYM3" in text
    assert "SYM0" not in text


@respx.mock
def test_alerts_command_empty(db: None) -> None:
    _mock_get_updates([_message_update("/alerts")])
    reply = _mock_send_message()
    bot = _make_bot()
    bot.poll_once()
    text = json.loads(reply.calls.last.request.content)["text"]
    assert "No alerts" in text


# ----------------------------------------------------------------------
# /jobs command
# ----------------------------------------------------------------------
@respx.mock
def test_jobs_command_lists_latest_per_job(db: None) -> None:
    base = datetime(2026, 5, 2, 1, 0, tzinfo=UTC)
    with get_session() as session:
        # Two runs of evening_pipeline; only the newer should appear.
        session.add(
            JobRun(
                job_name="evening_pipeline",
                started_at=base - timedelta(days=1),
                ended_at=base - timedelta(days=1) + timedelta(seconds=5),
                status="failure",
            )
        )
        session.add(
            JobRun(
                job_name="evening_pipeline",
                started_at=base,
                ended_at=base + timedelta(seconds=10),
                status="success",
            )
        )
        session.add(
            JobRun(
                job_name="morning_digest",
                started_at=base + timedelta(hours=4),
                ended_at=base + timedelta(hours=4, seconds=2),
                status="success",
            )
        )

    _mock_get_updates([_message_update("/jobs")])
    reply = _mock_send_message()
    bot = _make_bot()
    bot.poll_once()

    text = json.loads(reply.calls.last.request.content)["text"]
    assert "Jobs" in text
    assert "evening\\_pipeline" in text
    assert "morning\\_digest" in text
    # Only the newer evening_pipeline run is shown → its status is "success", not "failure".
    assert "failure" not in text


@respx.mock
def test_jobs_command_empty(db: None) -> None:
    _mock_get_updates([_message_update("/jobs")])
    reply = _mock_send_message()
    bot = _make_bot()
    bot.poll_once()
    text = json.loads(reply.calls.last.request.content)["text"]
    assert "No job runs" in text


# ----------------------------------------------------------------------
# /help text mentions the new commands
# ----------------------------------------------------------------------
@respx.mock
def test_help_text_lists_new_commands(db: None) -> None:
    _mock_get_updates([_message_update("/help")])
    reply = _mock_send_message()
    bot = _make_bot()
    bot.poll_once()

    text = json.loads(reply.calls.last.request.content)["text"]
    for cmd in ("/macro", "/earnings", "/positions", "/ticker", "/alerts", "/jobs"):
        # MarkdownV2 escapes "/" as "/" (no escape) but the body itself should
        # still contain the literal command tokens.
        assert cmd in text
