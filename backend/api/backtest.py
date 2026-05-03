"""Backtest resource — run launcher, run history, per-run trade detail."""

from __future__ import annotations

import statistics
from datetime import date as DateType
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from backtest.filter_backtest import run_filter_backtest
from core.logging import get_logger
from db import get_session
from db.models.backtest import BacktestRun, BacktestTrade
from db.models.screener import FilterConfig

log = get_logger(__name__)

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class BacktestTradeOut(BaseModel):
    id: int
    symbol: str
    entry_date: DateType
    exit_date: DateType | None
    entry_price: float
    exit_price: float | None
    outcome: str | None
    realized_pnl_pct: float | None


class BacktestRunOut(BaseModel):
    id: int
    config_id: int | None
    config_name: str | None
    start_date: DateType
    end_date: DateType
    params_json: dict[str, Any] | None
    created_at: datetime
    trade_count: int
    win_rate: float | None
    mean_return_pct: float | None
    median_return_pct: float | None


class BacktestRunIn(BaseModel):
    config_id: int
    start_date: DateType
    end_date: DateType
    forward_days: int = Field(default=30, ge=1, le=252)
    symbols: list[str] | None = None


@router.get("/runs", response_model=list[BacktestRunOut])
def list_runs() -> list[BacktestRunOut]:
    with get_session() as session:
        runs = (
            session.execute(select(BacktestRun).order_by(desc(BacktestRun.created_at)))
            .scalars()
            .all()
        )
        return [_build_run_out(session, run) for run in runs]


@router.post("/runs", response_model=BacktestRunOut, status_code=201)
def create_run(payload: BacktestRunIn) -> BacktestRunOut:
    with get_session() as session:
        config = session.get(FilterConfig, payload.config_id)
        if config is None:
            raise HTTPException(
                status_code=400, detail=f"config not found: {payload.config_id}"
            )
        if payload.end_date <= payload.start_date:
            raise HTTPException(
                status_code=400, detail="end_date must be after start_date"
            )
        try:
            run_id = run_filter_backtest(
                session,
                config_id=payload.config_id,
                start_date=payload.start_date,
                end_date=payload.end_date,
                forward_days=payload.forward_days,
                symbols=payload.symbols,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        run = session.get(BacktestRun, run_id)
        assert run is not None
        result = _build_run_out(session, run)
        log.info("backtest.api.run.created", run_id=run_id, trade_count=result.trade_count)
        return result


@router.get("/runs/{run_id}", response_model=BacktestRunOut)
def get_run(run_id: int) -> BacktestRunOut:
    with get_session() as session:
        run = session.get(BacktestRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        return _build_run_out(session, run)


@router.get("/runs/{run_id}/trades", response_model=list[BacktestTradeOut])
def list_trades(run_id: int) -> list[BacktestTradeOut]:
    with get_session() as session:
        run = session.get(BacktestRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        trades = (
            session.execute(
                select(BacktestTrade)
                .where(BacktestTrade.run_id == run_id)
                .order_by(BacktestTrade.entry_date, BacktestTrade.symbol)
            )
            .scalars()
            .all()
        )
        return [_trade_out(t) for t in trades]


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(run_id: int) -> Response:
    with get_session() as session:
        run = session.get(BacktestRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        session.delete(run)
        log.info("backtest.api.run.deleted", run_id=run_id)
    return Response(status_code=204)


def _build_run_out(session: Session, run: BacktestRun) -> BacktestRunOut:
    config_name: str | None = None
    if run.config_id is not None:
        config = session.get(FilterConfig, run.config_id)
        config_name = config.name if config else None

    trades = (
        session.execute(select(BacktestTrade).where(BacktestTrade.run_id == run.id))
        .scalars()
        .all()
    )
    returns = [t.realized_pnl / 100.0 for t in trades if t.realized_pnl is not None]
    wins = [r for r in returns if r > 0]

    return BacktestRunOut(
        id=run.id,
        config_id=run.config_id,
        config_name=config_name,
        start_date=run.start_date,
        end_date=run.end_date,
        params_json=run.params_json,
        created_at=run.created_at,
        trade_count=len(trades),
        win_rate=len(wins) / len(returns) if returns else None,
        mean_return_pct=statistics.mean(returns) * 100 if returns else None,
        median_return_pct=statistics.median(returns) * 100 if returns else None,
    )


def _trade_out(t: BacktestTrade) -> BacktestTradeOut:
    return BacktestTradeOut(
        id=t.id,
        symbol=t.symbol,
        entry_date=t.entry_date,
        exit_date=t.exit_date,
        entry_price=t.entry_price,
        exit_price=t.exit_price,
        outcome=t.outcome,
        realized_pnl_pct=t.realized_pnl / 100.0 if t.realized_pnl is not None else None,
    )
