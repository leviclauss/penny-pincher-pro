"""Tests for the ntfy.sh channel adapter.

Mocks the ntfy HTTP API via respx — no real network. Covers:
- Skip-not-fail when topic is missing.
- Request shape (URL, Title header, body, optional auth).
- Retry on 5xx + 429 with exponential backoff.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from alerts.channels.ntfy import NtfyChannel

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "alerts"


def _payload() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES_DIR / "iv_spike.json").read_text())
    return data


@pytest.fixture(autouse=True)
def _ntfy_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("NTFY_SERVER_URL", "https://ntfy.example.com")
    monkeypatch.setenv("NTFY_TOPIC", "wheel-alerts")
    monkeypatch.setenv("NTFY_TOKEN", "")
    monkeypatch.setenv("NTFY_PRIORITY", "default")

    from core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_skips_when_topic_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NTFY_TOPIC", "")
    from core.config import get_settings

    get_settings.cache_clear()
    result = NtfyChannel().send("iv_spike", _payload())
    assert result.delivered is False
    assert result.error == "ntfy_not_configured"


@respx.mock
def test_send_posts_to_topic_url() -> None:
    route = respx.post("https://ntfy.example.com/wheel-alerts").mock(
        return_value=httpx.Response(200, json={"id": "abc123"})
    )
    result = NtfyChannel().send("iv_spike", _payload())

    assert result.delivered is True
    assert result.provider_message_id == "abc123"
    request = route.calls.last.request
    assert request.headers["Title"].startswith("IV spike")
    assert request.headers["Priority"] == "default"
    assert request.headers["Tags"] == "iv_spike"
    assert "Authorization" not in request.headers
    assert b"ATM IV" in request.content


@respx.mock
def test_send_includes_bearer_token_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NTFY_TOKEN", "tk_secret")
    from core.config import get_settings

    get_settings.cache_clear()

    route = respx.post("https://ntfy.example.com/wheel-alerts").mock(
        return_value=httpx.Response(200, json={"id": "x"})
    )
    NtfyChannel().send("iv_spike", _payload())
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer tk_secret"


@respx.mock
def test_retries_on_5xx_then_succeeds() -> None:
    route = respx.post("https://ntfy.example.com/wheel-alerts").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(503),
            httpx.Response(200, json={"id": "ok"}),
        ]
    )
    result = NtfyChannel().send("iv_spike", _payload())
    assert result.delivered is True
    assert route.call_count == 3


@respx.mock
def test_retries_on_429() -> None:
    route = respx.post("https://ntfy.example.com/wheel-alerts").mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(200, json={"id": "ok"}),
        ]
    )
    result = NtfyChannel().send("iv_spike", _payload())
    assert result.delivered is True
    assert route.call_count == 2


@respx.mock
def test_non_retryable_4xx_returns_failure() -> None:
    respx.post("https://ntfy.example.com/wheel-alerts").mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    result = NtfyChannel().send("iv_spike", _payload())
    assert result.delivered is False
    assert result.error is not None
