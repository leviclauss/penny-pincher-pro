"""Telegram outbound channel adapter.

Implements the ``Channel`` protocol on top of the Bot API's ``sendMessage``
endpoint. HTTP-only — the long-poll consumer for inbound commands lands in
phase 3 and brings the ``python-telegram-bot`` dep with it.

Behavior:
- Without ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID`` the channel reports
  ``delivered=False`` and logs a warning, mirroring the silent-no-op shape
  the Finnhub earnings step uses for missing keys.
- Renders the alert via ``alerts.templates.telegram_render`` and splits the
  result into ≤4096-char chunks before sending.
- Retries transient transport / 5xx / 429 responses with exponential backoff
  via tenacity; non-retryable 4xx (token revoked, chat id wrong, malformed
  entities) raise after a single attempt.
- 400 ``can't parse entities`` falls back to ``parse_mode=None`` so a
  template bug doesn't lose the alert outright; the original failure is
  logged at ERROR.
"""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from alerts.channels.base import ChannelResult
from alerts.templates.telegram_render import render, split_for_telegram
from core.config import get_settings
from core.logging import get_logger

log = get_logger(__name__)

API_BASE = "https://api.telegram.org"

_RETRYABLE_EXCEPTIONS = (
    httpx.TransportError,
    httpx.TimeoutException,
)


class _RetryableStatus(Exception):
    """Raised on transient HTTP status codes (5xx / 429) to drive tenacity."""


class TelegramChannel:
    id = "telegram"

    def __init__(self, client: httpx.Client | None = None) -> None:
        settings = get_settings()
        self._token = settings.telegram_bot_token
        self._chat_id = settings.telegram_chat_id
        self._parse_mode = settings.telegram_parse_mode
        self._disable_preview = settings.telegram_disable_preview
        self._web_base_url = settings.web_base_url
        self._client = client or httpx.Client(timeout=settings.telegram_timeout_s)

    @property
    def configured(self) -> bool:
        return bool(self._token and self._chat_id)

    def send(
        self,
        alert_type: str,
        payload: dict[str, Any],
        *,
        alert_id: int | None = None,
    ) -> ChannelResult:
        if not self.configured:
            log.warning("telegram.skip.unconfigured", alert_type=alert_type)
            return ChannelResult(False, None, "telegram_not_configured")

        try:
            text = render(
                alert_type,
                payload,
                parse_mode=self._parse_mode,
                web_base_url=self._web_base_url,
            )
        except Exception as exc:
            log.error("telegram.render.failed", alert_type=alert_type, error=str(exc))
            return ChannelResult(False, None, f"render_failed: {exc}")

        chunks = split_for_telegram(text)
        first_message_id: str | None = None
        for index, chunk in enumerate(chunks):
            # Only attach the inline ack keyboard to the *last* chunk so the
            # button stays visible at the bottom of a multi-message digest.
            reply_markup = (
                _build_ack_keyboard(alert_id)
                if alert_id is not None and index == len(chunks) - 1
                else None
            )
            try:
                message_id = self._send_one(chunk, alert_type=alert_type, reply_markup=reply_markup)
            except Exception as exc:
                log.error(
                    "telegram.send.failed",
                    alert_type=alert_type,
                    chunk_index=index,
                    error=str(exc),
                )
                return ChannelResult(False, first_message_id, str(exc))
            if first_message_id is None:
                first_message_id = message_id

        log.info(
            "telegram.sent",
            alert_type=alert_type,
            chunks=len(chunks),
            message_id=first_message_id,
            alert_id=alert_id,
        )
        return ChannelResult(True, first_message_id, None)

    @retry(
        retry=retry_if_exception_type((*_RETRYABLE_EXCEPTIONS, _RetryableStatus)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _send_one(
        self,
        text: str,
        *,
        alert_type: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> str:
        url = f"{API_BASE}/bot{self._token}/sendMessage"
        body: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": self._parse_mode,
            "disable_web_page_preview": self._disable_preview,
        }
        if reply_markup is not None:
            body["reply_markup"] = reply_markup
        response = self._client.post(url, json=body)
        if response.status_code == 429:
            retry_after = _retry_after_seconds(response)
            log.warning(
                "telegram.rate_limited",
                alert_type=alert_type,
                retry_after=retry_after,
            )
            raise _RetryableStatus(f"429 retry_after={retry_after}")
        if 500 <= response.status_code < 600:
            raise _RetryableStatus(f"{response.status_code}")
        if response.status_code == 400 and _is_parse_error(response):
            log.error(
                "telegram.parse_error.fallback_plaintext",
                alert_type=alert_type,
                detail=_safe_description(response),
            )
            return self._send_plaintext(text)
        response.raise_for_status()
        return _extract_message_id(response)

    def _send_plaintext(self, text: str) -> str:
        url = f"{API_BASE}/bot{self._token}/sendMessage"
        body = {
            "chat_id": self._chat_id,
            "text": text,
            "disable_web_page_preview": self._disable_preview,
        }
        response = self._client.post(url, json=body)
        response.raise_for_status()
        return _extract_message_id(response)


def _build_ack_keyboard(alert_id: int) -> dict[str, Any]:
    """Inline keyboard with a single Ack button keyed by alert_id."""
    return {
        "inline_keyboard": [
            [{"text": "✓ Ack", "callback_data": f"ack:{alert_id}"}],
        ],
    }


def _retry_after_seconds(response: httpx.Response) -> int:
    try:
        params = response.json().get("parameters", {})
        value = params.get("retry_after")
        if isinstance(value, int):
            return value
    except ValueError:
        pass
    return 1


def _is_parse_error(response: httpx.Response) -> bool:
    description = _safe_description(response)
    return "can't parse entities" in description.lower()


def _safe_description(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    description = body.get("description") if isinstance(body, dict) else None
    return description if isinstance(description, str) else ""


def _extract_message_id(response: httpx.Response) -> str:
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError(f"unexpected response shape: {type(body)!r}")
    result = body.get("result")
    if not isinstance(result, dict):
        raise ValueError("missing result in telegram response")
    message_id = result.get("message_id")
    if message_id is None:
        raise ValueError("missing message_id in telegram response")
    return str(message_id)
