"""Time helpers. All timestamps in the DB are timezone-aware UTC."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")


def utcnow() -> datetime:
    return datetime.now(UTC)


def market_today() -> date:
    return datetime.now(MARKET_TZ).date()
