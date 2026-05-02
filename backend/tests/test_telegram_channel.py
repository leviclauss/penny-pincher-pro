"""Tests for the Telegram outbound channel adapter.

Mocks the Bot API via respx — no real network. Covers:
- Skip-not-fail when token / chat id are missing.
- Request shape (endpoint, body keys, parse mode).
- Retry on 5xx + 429 with exponential backoff (compressed via monkeypatch).
- 400 ``can't parse entities`` falls back to plain text.
- Long renders are split into ≤4096-char chunks.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
import respx

from alerts.channels.telegram import TelegramChannel
from alerts.templates import telegram_render


@pytest.fixture(autouse=True)
def _telegram_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tkn")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("TELEGRAM_PARSE_MODE", "MarkdownV2")
    monkeypatch.setenv("TELEGRAM_DISABLE_PREVIEW", "true")
    from core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _payload() -> dict[str, object]:
    return {
        "as_of": "2026-05-02",
        "macro": {"vix": 14.2, "spy_above_200ema": True, "term": 0.92},
        "screener_hits": [
            {
                "symbol": "AAPL",
                "config": "Conservative Wheel",
                "close": 172.40,
                "rsi": 32,
                "ivp": 67,
                "score": 0.81,
                "next_earnings_days": 38,
            }
        ],
        "earnings_today": [{"symbol": "MSFT", "when": "AMC"}],
        "positions_attention": [],
    }


def test_skips_when_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    from core.config import get_settings

    get_settings.cache_clear()
    result = TelegramChannel().send("morning_digest", _payload())
    assert result.delivered is False
    assert result.error == "telegram_not_configured"


@respx.mock
def test_send_posts_to_bot_api() -> None:
    route = respx.post("https://api.telegram.org/bottkn/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 100}})
    )
    result = TelegramChannel().send("morning_digest", _payload())

    assert result.delivered is True
    assert result.provider_message_id == "100"
    request = route.calls.last.request
    body = json.loads(request.content)
    assert body["chat_id"] == "42"
    assert body["parse_mode"] == "MarkdownV2"
    assert body["disable_web_page_preview"] is True
    assert "Morning Digest" in body["text"]
    # MarkdownV2 escaping turns "." into "\."
    assert "2026\\-05\\-02" in body["text"]


@respx.mock
def test_retries_on_5xx_then_succeeds() -> None:
    route = respx.post("https://api.telegram.org/bottkn/sendMessage").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(503),
            httpx.Response(200, json={"ok": True, "result": {"message_id": 7}}),
        ]
    )
    result = TelegramChannel().send("morning_digest", _payload())
    assert result.delivered is True
    assert route.call_count == 3


@respx.mock
def test_retries_on_429_with_retry_after() -> None:
    route = respx.post("https://api.telegram.org/bottkn/sendMessage").mock(
        side_effect=[
            httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 1}}),
            httpx.Response(200, json={"ok": True, "result": {"message_id": 9}}),
        ]
    )
    result = TelegramChannel().send("morning_digest", _payload())
    assert result.delivered is True
    assert route.call_count == 2


@respx.mock
def test_parse_error_falls_back_to_plaintext() -> None:
    route = respx.post("https://api.telegram.org/bottkn/sendMessage").mock(
        side_effect=[
            httpx.Response(
                400,
                json={"ok": False, "description": "Bad Request: can't parse entities"},
            ),
            httpx.Response(200, json={"ok": True, "result": {"message_id": 11}}),
        ]
    )
    result = TelegramChannel().send("morning_digest", _payload())
    assert result.delivered is True
    assert route.call_count == 2
    fallback = json.loads(route.calls.last.request.content)
    assert "parse_mode" not in fallback


@respx.mock
def test_non_retryable_4xx_returns_failure() -> None:
    respx.post("https://api.telegram.org/bottkn/sendMessage").mock(
        return_value=httpx.Response(401, json={"ok": False, "description": "Unauthorized"})
    )
    result = TelegramChannel().send("morning_digest", _payload())
    assert result.delivered is False
    assert result.provider_message_id is None
    assert result.error is not None


@respx.mock
def test_long_payload_is_split_into_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render a synthetic > 4096-char message; verify two POSTs are made."""
    monkeypatch.setattr(telegram_render, "MAX_MESSAGE_CHARS", 200)

    route = respx.post("https://api.telegram.org/bottkn/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    )
    long_payload = _payload()
    long_payload["screener_hits"] = [
        {
            "symbol": f"SYM{i:02d}",
            "config": "Conservative Wheel",
            "close": 100.0 + i,
            "rsi": 30 + i,
            "ivp": 50 + i,
            "score": 0.5,
            "next_earnings_days": 30,
        }
        for i in range(20)
    ]
    result = TelegramChannel().send("morning_digest", long_payload)
    assert result.delivered is True
    assert route.call_count >= 2
