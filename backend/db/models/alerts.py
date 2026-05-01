"""Alert history and per-type delivery preferences."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column

from core.time import utcnow
from db.session import Base


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    symbol: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    config_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    channels_sent: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_acked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AlertPreference(Base):
    __tablename__ = "alert_preferences"

    alert_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    channels: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    quiet_hours_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    quiet_hours_end: Mapped[time | None] = mapped_column(Time, nullable=True)
