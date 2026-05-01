"""Screener: filter configs and per-day results."""

from __future__ import annotations

from datetime import date as DateType
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.time import utcnow
from db.session import Base


class FilterConfig(Base):
    __tablename__ = "filter_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ScreenerResult(Base):
    __tablename__ = "screener_results"

    date: Mapped[DateType] = mapped_column(Date, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    config_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("filter_configs.id"), primary_key=True
    )

    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    filter_results_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    target_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_expiration: Mapped[DateType | None] = mapped_column(Date, nullable=True)
    target_premium: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    annualized_return: Mapped[float | None] = mapped_column(Float, nullable=True)
