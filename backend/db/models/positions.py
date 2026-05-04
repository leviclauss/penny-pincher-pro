"""Wheel position lifecycle: positions, legs, daily snapshots."""

from __future__ import annotations

from datetime import date as DateType
from datetime import datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.time import utcnow
from db.session import Base


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    cycle_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    acquisition_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    portfolio_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("portfolios.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class PositionLeg(Base):
    __tablename__ = "position_legs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("positions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    leg_type: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    expiration: Mapped[DateType | None] = mapped_column(Date, nullable=True)
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    contracts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_date: Mapped[DateType | None] = mapped_column(Date, nullable=True)
    exit_date: Mapped[DateType | None] = mapped_column(Date, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


class PositionSnapshot(Base):
    __tablename__ = "position_snapshots"

    position_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("positions.id", ondelete="CASCADE"), primary_key=True
    )
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    underlying_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    option_mid: Mapped[float | None] = mapped_column(Float, nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    pct_max_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    dte: Mapped[int | None] = mapped_column(Integer, nullable=True)
