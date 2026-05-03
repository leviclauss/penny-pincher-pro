"""ntfy.sh outbound channel adapter.

Implements the ``Channel`` protocol against ntfy's HTTP API: a POST to
``<server>/<topic>`` with the body as the message and the ``Title`` header
carrying the notification title. Works against ntfy.sh and self-hosted
instances; auth is optional (Bearer token).

Behavior:
- Without ``NTFY_TOPIC`` (or ``NTFY_SERVER_URL``) the channel reports
  ``delivered=False`` and logs a warning, mirroring the Telegram adapter.
- Transient transport / 5xx / 429 responses retry with exponential backoff
  via tenacity; non-retryable 4xx surface as ``delivered=False``.
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

from alerts.channels.base import Channel, ChannelResult
from alerts.templates.ntfy_render import render
from core.config import get_settings
from core.logging import get_logger

log = get_logger(__name__)

_RETRYABLE_EXCEPTIONS = (
    httpx.TransportError,
    httpx.TimeoutException,
)


class _RetryableStatus(Exception):
    """Raised on transient HTTP status codes (5xx / 429) to drive tenacity."""


class NtfyChannel(Channel):
    id = "ntfy"

    def __init__(self, client: httpx.Client | None = None) -> None:
        settings = get_settings()
        self._server_url = settings.ntfy_server_url.rstrip("/")
        self._topic = settings.ntfy_topic
        self._token = settings.ntfy_token
        self._priority = settings.ntfy_priority
        self._web_base_url = settings.web_base_url
        self._client = client or httpx.Client(timeout=settings.ntfy_timeout_s)

    @property
    def configured(self) -> bool:
        return bool(self._server_url and self._topic)

    def send(
        self, alert_type: str, payload: dict[str, Any], *, alert_id: int | None = None
    ) -> ChannelResult:
        if not self.configured:
            log.warning("ntfy.skip.unconfigured", alert_type=alert_type)
            return ChannelResult(False, None, "ntfy_not_configured")

        try:
            message = render(alert_type, payload, web_base_url=self._web_base_url)
        except Exception as exc:
            log.error("ntfy.render.failed", alert_type=alert_type, error=str(exc))
            return ChannelResult(False, None, f"render_failed: {exc}")

        try:
            provider_id = self._post(message.title, message.body, alert_type=alert_type)
        except Exception as exc:
            log.error("ntfy.send.failed", alert_type=alert_type, error=str(exc))
            return ChannelResult(False, None, str(exc))

        log.info(
            "ntfy.sent",
            alert_type=alert_type,
            topic=self._topic,
            provider_id=provider_id,
        )
        return ChannelResult(True, provider_id, None)

    @retry(
        retry=retry_if_exception_type((*_RETRYABLE_EXCEPTIONS, _RetryableStatus)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _post(self, title: str, body: str, *, alert_type: str) -> str | None:
        url = f"{self._server_url}/{self._topic}"
        # ntfy headers must be latin-1 encodable. Strip / replace anything
        # outside that range so an em-dash in a template doesn't blow up the
        # send. Body is sent UTF-8 so unicode there is fine.
        headers: dict[str, str] = {
            "Title": _ascii_safe(title),
            "Priority": self._priority,
            "Tags": alert_type,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        response = self._client.post(url, content=body.encode("utf-8"), headers=headers)
        if response.status_code == 429:
            log.warning("ntfy.rate_limited", alert_type=alert_type)
            raise _RetryableStatus("429")
        if 500 <= response.status_code < 600:
            raise _RetryableStatus(f"{response.status_code}")
        response.raise_for_status()
        return _extract_message_id(response)


# Map common typographically-quoted characters that templates may emit to
# their ASCII equivalents so the ntfy ``Title`` header (which must be
# latin-1 encodable) doesn't reject the message.
_HEADER_TRANSLATIONS = str.maketrans(
    {
        "—": "-",  # em dash
        "–": "-",  # en dash
        "…": "...",  # ellipsis
        "“": '"',  # left double quote
        "”": '"',  # right double quote
        "‘": "'",  # left single quote
        "’": "'",  # right single quote
    }
)


def _ascii_safe(value: str) -> str:
    translated = value.translate(_HEADER_TRANSLATIONS)
    return translated.encode("ascii", "replace").decode("ascii")


def _extract_message_id(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    message_id = body.get("id")
    return str(message_id) if message_id is not None else None
