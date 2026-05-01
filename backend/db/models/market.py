"""Market data: tickers, bars, indicators, options, earnings, macro.

These tables are produced by the ingestion track and consumed by the screener
track. They are the schema contract between the two tracks — additions are
welcome, but column renames or type changes require coordination.
"""

from __future__ import annotations

from datetime import date as DateType
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.time import utcnow
from db.session import Base


class Ticker(Base):
    __tablename__ = "tickers"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class BarDaily(Base):
    __tablename__ = "bars_daily"

    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol"), primary_key=True)
    date: Mapped[DateType] = mapped_column(Date, primary_key=True)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (Index("ix_bars_daily_symbol_date_desc", "symbol", "date"),)


class IndicatorDaily(Base):
    __tablename__ = "indicators_daily"

    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol"), primary_key=True)
    date: Mapped[DateType] = mapped_column(Date, primary_key=True)

    ema_20: Mapped[float | None] = mapped_column(Float, nullable=True)
    ema_50: Mapped[float | None] = mapped_column(Float, nullable=True)
    ema_200: Mapped[float | None] = mapped_column(Float, nullable=True)
    ema_200_weekly: Mapped[float | None] = mapped_column(Float, nullable=True)

    rsi_14: Mapped[float | None] = mapped_column(Float, nullable=True)
    atr_14: Mapped[float | None] = mapped_column(Float, nullable=True)

    bb_upper: Mapped[float | None] = mapped_column(Float, nullable=True)
    bb_lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    bb_mid: Mapped[float | None] = mapped_column(Float, nullable=True)

    iv_atm: Mapped[float | None] = mapped_column(Float, nullable=True)
    iv_rank: Mapped[float | None] = mapped_column(Float, nullable=True)
    iv_percentile: Mapped[float | None] = mapped_column(Float, nullable=True)
    hv_20: Mapped[float | None] = mapped_column(Float, nullable=True)


class OptionsSnapshot(Base):
    __tablename__ = "options_snapshot"

    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol"), primary_key=True)
    expiration: Mapped[DateType] = mapped_column(Date, primary_key=True)
    strike: Mapped[float] = mapped_column(Float, primary_key=True)
    option_type: Mapped[str] = mapped_column(String(4), primary_key=True)

    bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    last: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    open_interest: Mapped[int | None] = mapped_column(Integer, nullable=True)

    delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    gamma: Mapped[float | None] = mapped_column(Float, nullable=True)
    theta: Mapped[float | None] = mapped_column(Float, nullable=True)
    vega: Mapped[float | None] = mapped_column(Float, nullable=True)
    iv: Mapped[float | None] = mapped_column(Float, nullable=True)

    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Earnings(Base):
    __tablename__ = "earnings"

    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol"), primary_key=True)
    earnings_date: Mapped[DateType] = mapped_column(Date, primary_key=True)
    time_of_day: Mapped[str | None] = mapped_column(String(16), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class MacroDaily(Base):
    __tablename__ = "macro_daily"

    date: Mapped[DateType] = mapped_column(Date, primary_key=True)
    vix_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    vix_9d: Mapped[float | None] = mapped_column(Float, nullable=True)
    vix_term_structure: Mapped[float | None] = mapped_column(Float, nullable=True)
    spy_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    spy_ema_200: Mapped[float | None] = mapped_column(Float, nullable=True)
    spy_above_200ema: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
