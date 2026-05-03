"""Tests for the SMTP email channel adapter.

Mocks ``smtplib.SMTP`` — no real network. Covers:
- Skip-not-fail when host / from / to are missing.
- Happy-path delivery + correct subject/from/to.
- STARTTLS + login are exercised when configured.
- Send failure surfaces as ``ChannelResult(delivered=False)``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from email.message import EmailMessage as MIMEEmailMessage
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from alerts.channels.email import EmailChannel

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "alerts"


def _payload() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES_DIR / "morning_digest.json").read_text())
    return data


@pytest.fixture
def _email_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "alerts@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "shh")
    monkeypatch.setenv("SMTP_FROM_ADDRESS", "alerts@example.com")
    monkeypatch.setenv("SMTP_TO_ADDRESS", "me@example.com")
    monkeypatch.setenv("SMTP_USE_TLS", "true")
    monkeypatch.setenv("SMTP_USE_SSL", "false")

    from core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_skips_when_host_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("SMTP_FROM_ADDRESS", "")
    monkeypatch.setenv("SMTP_TO_ADDRESS", "")

    from core.config import get_settings

    get_settings.cache_clear()
    result = EmailChannel().send("morning_digest", _payload())
    assert result.delivered is False
    assert result.error == "email_not_configured"


def test_send_delivers_via_smtp(_email_env: None) -> None:
    fake_client = MagicMock()
    with patch("alerts.channels.email.smtplib.SMTP", return_value=fake_client) as ctor:
        result = EmailChannel().send("morning_digest", _payload())

    assert result.delivered is True
    ctor.assert_called_once_with("smtp.example.com", 587, timeout=10.0)
    fake_client.starttls.assert_called_once()
    fake_client.login.assert_called_once_with("alerts@example.com", "shh")
    fake_client.send_message.assert_called_once()
    sent: MIMEEmailMessage = fake_client.send_message.call_args.args[0]
    assert sent["From"] == "alerts@example.com"
    assert sent["To"] == "me@example.com"
    assert "Morning digest" in sent["Subject"]
    body = sent.get_content()
    assert "Macro" in body
    fake_client.quit.assert_called_once()


def test_send_skips_login_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "25")
    monkeypatch.setenv("SMTP_USER", "")
    monkeypatch.setenv("SMTP_PASSWORD", "")
    monkeypatch.setenv("SMTP_FROM_ADDRESS", "alerts@example.com")
    monkeypatch.setenv("SMTP_TO_ADDRESS", "me@example.com")
    monkeypatch.setenv("SMTP_USE_TLS", "false")

    from core.config import get_settings

    get_settings.cache_clear()

    fake_client = MagicMock()
    with patch("alerts.channels.email.smtplib.SMTP", return_value=fake_client):
        result = EmailChannel().send("morning_digest", _payload())

    assert result.delivered is True
    fake_client.starttls.assert_not_called()
    fake_client.login.assert_not_called()
    fake_client.send_message.assert_called_once()
    get_settings.cache_clear()


def test_uses_smtp_ssl_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    monkeypatch.setenv("SMTP_FROM_ADDRESS", "a@example.com")
    monkeypatch.setenv("SMTP_TO_ADDRESS", "b@example.com")
    monkeypatch.setenv("SMTP_USE_SSL", "true")

    from core.config import get_settings

    get_settings.cache_clear()

    fake_client = MagicMock()
    with (
        patch("alerts.channels.email.smtplib.SMTP_SSL", return_value=fake_client) as ssl_ctor,
        patch("alerts.channels.email.smtplib.SMTP") as plain_ctor,
    ):
        result = EmailChannel().send("morning_digest", _payload())

    assert result.delivered is True
    ssl_ctor.assert_called_once()
    plain_ctor.assert_not_called()
    fake_client.starttls.assert_not_called()
    get_settings.cache_clear()


def test_send_returns_failure_when_smtp_raises(_email_env: None) -> None:
    fake_client = MagicMock()
    fake_client.send_message.side_effect = OSError("connection refused")
    with patch("alerts.channels.email.smtplib.SMTP", return_value=fake_client):
        result = EmailChannel().send("morning_digest", _payload())

    assert result.delivered is False
    assert result.error is not None
    assert "connection refused" in result.error
