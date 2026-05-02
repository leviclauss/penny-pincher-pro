"""Channel protocol shared by every notification adapter.

Each adapter implements ``send(alert_type, payload)`` and reports the outcome
via ``ChannelResult``. The dispatcher (``alerts.dispatcher``) fans payloads
across configured channels per ``alert_preferences``.
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

    def send(self, alert_type: str, payload: dict[str, Any]) -> ChannelResult: ...
