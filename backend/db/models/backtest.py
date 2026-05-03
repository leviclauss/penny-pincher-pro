"""Backtesting: run metadata, simulated trades, and equity curves."""

from __future__ import annotations

from datetime import date as DateType
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.time import utcnow
from db.session import Base

MODE_FILTER = "filter"
MODE_STRATEGY = "strategy"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    config_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("filter_configs.id"), nullable=True
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default=MODE_FILTER)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_COMPLETED, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_date: Mapped[DateType] = mapped_column(Date, nullable=False)
    end_date: Mapped[DateType] = mapped_column(Date, nullable=False)
    starting_capital: Mapped[float] = mapped_column(Float, nullable=False)
    params_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cycle_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    leg_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entry_date: Mapped[DateType] = mapped_column(Date, nullable=False)
    exit_date: Mapped[DateType | None] = mapped_column(Date, nullable=True)
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    expiration: Mapped[DateType | None] = mapped_column(Date, nullable=True)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # Per-leg diagnostic detail surfaced in the UI: contracts, shares,
    # premium, slippage, spot/sigma at entry+exit, intrinsic, cost basis,
    # P/L breakdown, and a free-form ``explanation`` string. Populated by
    # the simulator at each emission point; ``None`` for filter-mode rows.
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class BacktestEquity(Base):
    __tablename__ = "backtest_equity"

    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("backtest_runs.id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[DateType] = mapped_column(Date, primary_key=True)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    cash: Mapped[float] = mapped_column(Float, nullable=False)
    collateral_locked: Mapped[float] = mapped_column(Float, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Float, nullable=False)
