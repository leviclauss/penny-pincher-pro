"""Alert fan-out: render once per channel, persist a row per fired alert.

Reads ``alert_preferences`` for the alert type to decide which channels to
hit. Missing preferences default to a single-channel ``["telegram"]`` config
with quiet hours disabled — that lets phase 1 work before the screener
pipeline is seeding rows.

The dispatcher is best-effort per channel: each enabled channel renders
its own template and delivers independently. A failure on one channel logs
+ continues; the channel id is omitted from ``alerts.channels_sent`` but
the alert row is written regardless so the in-app history stays complete.
Multiple channels for the same fire produce **one** ``alerts`` row — channels
are a delivery detail, not a separate alert per channel.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from alerts.channels.base import Channel
from alerts.channels.email import EmailChannel
from alerts.channels.ntfy import NtfyChannel
from alerts.channels.telegram import TelegramChannel
from core.config import get_settings
from core.logging import get_logger
from core.time import utcnow
from db import get_session
from db.models.alerts import Alert, AlertPreference

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DispatchResult:
    alert_id: int | None
    channels_attempted: list[str]
    channels_sent: list[str]
    skipped_reason: str | None


@dataclass(frozen=True, slots=True)
class _ResolvedPreference:
    channels: list[str]
    enabled: bool
    quiet_hours_start: time | None
    quiet_hours_end: time | None


_DEFAULT_PREFERENCE = _ResolvedPreference(
    channels=["telegram"],
    enabled=True,
    quiet_hours_start=None,
    quiet_hours_end=None,
)


def _build_default_registry() -> dict[str, Channel]:
    return {
        "telegram": TelegramChannel(),
        "email": EmailChannel(),
        "ntfy": NtfyChannel(),
    }


CHANNELS: dict[str, Channel] = _build_default_registry()


def reset_registry() -> None:
    """Rebuild the default channel registry. Used by tests after env tweaks."""
    CHANNELS.clear()
    CHANNELS.update(_build_default_registry())


def dispatch(
    alert_type: str,
    payload: dict[str, Any],
    *,
    registry: dict[str, Channel] | None = None,
) -> DispatchResult:
    channels_registry = registry if registry is not None else CHANNELS
    preference = _load_preference(alert_type)

    if not preference.enabled:
        log.info("dispatch.disabled", alert_type=alert_type)
        return DispatchResult(None, [], [], "disabled")

    if _in_quiet_hours(preference):
        log.info("dispatch.quiet_hours", alert_type=alert_type)
        return DispatchResult(None, [], [], "quiet_hours")

    attempted: list[str] = []
    sent: list[str] = []
    for channel_id in preference.channels:
        attempted.append(channel_id)
        channel = channels_registry.get(channel_id)
        if channel is None:
            log.warning("dispatch.channel.unknown", channel=channel_id, alert_type=alert_type)
            continue
        try:
            result = channel.send(alert_type, payload)
        except Exception as exc:
            log.error(
                "dispatch.channel.exception",
                channel=channel_id,
                alert_type=alert_type,
                error=str(exc),
            )
            continue
        if result.delivered:
            sent.append(channel_id)
        else:
            log.warning(
                "dispatch.channel.failed",
                channel=channel_id,
                alert_type=alert_type,
                error=result.error,
            )

    alert_id = _persist_alert(alert_type, payload, sent)
    return DispatchResult(alert_id, attempted, sent, None)


def _load_preference(alert_type: str) -> _ResolvedPreference:
    with get_session() as session:
        row = session.execute(
            select(AlertPreference).where(AlertPreference.alert_type == alert_type)
        ).scalar_one_or_none()
        if row is None:
            return _DEFAULT_PREFERENCE
        channels = [c for c in row.channels if isinstance(c, str)] if row.channels else []
        return _ResolvedPreference(
            channels=channels or list(_DEFAULT_PREFERENCE.channels),
            enabled=row.enabled,
            quiet_hours_start=row.quiet_hours_start,
            quiet_hours_end=row.quiet_hours_end,
        )


def _in_quiet_hours(preference: _ResolvedPreference, *, now: datetime | None = None) -> bool:
    start = preference.quiet_hours_start
    end = preference.quiet_hours_end
    if start is None or end is None or start == end:
        return False

    settings = get_settings()
    tz = ZoneInfo(settings.timezone)
    current = (now or utcnow()).astimezone(tz).time()
    if start < end:
        return start <= current < end
    # Overnight window (e.g. 22:00 → 07:00).
    return current >= start or current < end


def _persist_alert(
    alert_type: str,
    payload: dict[str, Any],
    channels_sent: list[str],
) -> int:
    symbol = payload.get("symbol")
    config_id = payload.get("config_id")
    with get_session() as session:
        row = Alert(
            alert_type=alert_type,
            symbol=symbol if isinstance(symbol, str) else None,
            config_id=config_id if isinstance(config_id, int) else None,
            payload_json=payload,
            channels_sent=json.dumps(channels_sent) if channels_sent else None,
        )
        session.add(row)
        session.flush()
        alert_id = row.id
    return alert_id
