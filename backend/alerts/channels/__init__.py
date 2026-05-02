"""Notification channel adapters (email, push, webhook)."""

from alerts.channels.base import Channel, ChannelResult
from alerts.channels.telegram import TelegramChannel

__all__ = ["Channel", "ChannelResult", "TelegramChannel"]
