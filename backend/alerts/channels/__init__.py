"""Notification channel adapters (Telegram, email, ntfy, …)."""

from alerts.channels.base import Channel, ChannelResult
from alerts.channels.email import EmailChannel
from alerts.channels.ntfy import NtfyChannel
from alerts.channels.telegram import TelegramChannel

__all__ = [
    "Channel",
    "ChannelResult",
    "EmailChannel",
    "NtfyChannel",
    "TelegramChannel",
]
