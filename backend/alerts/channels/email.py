"""SMTP email outbound channel adapter.

Implements the ``Channel`` protocol on top of ``smtplib`` + ``EmailMessage``.
Plain-text only — no HTML, no attachments — so we don't need MIME juggling.

Behavior:
- Without ``SMTP_HOST`` / ``SMTP_FROM_ADDRESS`` / ``SMTP_TO_ADDRESS`` the
  channel reports ``delivered=False`` and logs a warning, mirroring the
  Telegram adapter's silent-no-op shape.
- Uses STARTTLS by default (``SMTP_USE_TLS=true``); set ``SMTP_USE_SSL=true``
  for implicit-TLS submission (port 465) and the channel uses ``SMTP_SSL``.
- Auth is attempted only when both user + password are set, so unauthenticated
  relays (LAN postfix, dev maildev) keep working.
- Connection / send errors are logged and surface as ``delivered=False``;
  the dispatcher continues with the next channel.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage as MIMEEmailMessage
from typing import Any

from alerts.channels.base import Channel, ChannelResult
from alerts.templates.email_render import render
from core.config import get_settings
from core.logging import get_logger

log = get_logger(__name__)


class EmailChannel(Channel):
    id = "email"

    def __init__(self) -> None:
        settings = get_settings()
        self._host = settings.smtp_host
        self._port = settings.smtp_port
        self._user = settings.smtp_user
        self._password = settings.smtp_password
        self._use_tls = settings.smtp_use_tls
        self._use_ssl = settings.smtp_use_ssl
        self._from_address = settings.smtp_from_address
        self._to_address = settings.smtp_to_address
        self._timeout_s = settings.smtp_timeout_s
        self._web_base_url = settings.web_base_url

    @property
    def configured(self) -> bool:
        return bool(self._host and self._from_address and self._to_address)

    def send(
        self, alert_type: str, payload: dict[str, Any], *, alert_id: int | None = None
    ) -> ChannelResult:
        if not self.configured:
            log.warning("email.skip.unconfigured", alert_type=alert_type)
            return ChannelResult(False, None, "email_not_configured")

        try:
            message = render(alert_type, payload, web_base_url=self._web_base_url)
        except Exception as exc:
            log.error("email.render.failed", alert_type=alert_type, error=str(exc))
            return ChannelResult(False, None, f"render_failed: {exc}")

        mime = MIMEEmailMessage()
        mime["Subject"] = message.subject
        mime["From"] = self._from_address
        mime["To"] = self._to_address
        mime.set_content(message.body or "(no body)")

        try:
            self._deliver(mime)
        except Exception as exc:
            log.error("email.send.failed", alert_type=alert_type, error=str(exc))
            return ChannelResult(False, None, str(exc))

        provider_id = mime.get("Message-ID")
        log.info(
            "email.sent",
            alert_type=alert_type,
            to=self._to_address,
            subject=message.subject,
        )
        return ChannelResult(True, provider_id, None)

    def _deliver(self, message: MIMEEmailMessage) -> None:
        client = self._build_client()
        try:
            if self._use_tls and not self._use_ssl:
                client.starttls()
            if self._user and self._password:
                client.login(self._user, self._password)
            client.send_message(message)
        finally:
            try:
                client.quit()
            except smtplib.SMTPException:
                client.close()

    def _build_client(self) -> smtplib.SMTP:
        if self._use_ssl:
            return smtplib.SMTP_SSL(self._host, self._port, timeout=self._timeout_s)
        return smtplib.SMTP(self._host, self._port, timeout=self._timeout_s)
