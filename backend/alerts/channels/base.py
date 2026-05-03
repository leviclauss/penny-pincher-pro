"""Channel protocol shared by every notification adapter.

Each adapter implements ``send(alert_type, payload)`` and reports the outcome
via ``ChannelResult``. The dispatcher (``alerts.dispatcher``) fans payloads
across configured channels per ``alert_preferences``.

The optional ``alert_id`` keyword carries the freshly-persisted alert row
id so channels that support interactive replies (currently Telegram, via
inline ack buttons) can echo it back through ``callback_data``. Channels
that don't need it ignore the kwarg.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ChannelResult:
    delivered: bool
    provider_message_id: str | None
    error: str | None


@runtime_checkable
class Channel(Protocol):
    id: str

    def send(
        self,
        alert_type: str,
        payload: dict[str, Any],
        *,
        alert_id: int | None = None,
    ) -> ChannelResult: ...
