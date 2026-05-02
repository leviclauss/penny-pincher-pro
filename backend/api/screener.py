"""Screener resource — configs and per-day results.

Read-only for now. Config CRUD lands in a later session along with the UI
form for tuning thresholds.
"""

from __future__ import annotations

from datetime import date as DateType
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from db import get_session
from db.models.market import Earnings, IndicatorDaily, Ticker
from db.models.screener import FilterConfig, ScreenerResult

router = APIRouter(prefix="/api/screener", tags=["screener"])


class FilterConfigSummary(BaseModel):
    id: int
    name: str
    description: str | None
    is_active: bool
    filter_ids: list[str]


class FilterConfigDetail(FilterConfigSummary):
    config_json: dict[str, Any]


class ScreenerResultRow(BaseModel):
    date: DateType
    symbol: str
    config_id: int
    passed: bool
    score: float | None
    sector: str | None
    rsi_14: float | None
    iv_rank: float | None
    iv_percentile: float | None
    near_200ema_pct: float | None
    next_earnings_date: DateType | None
    filter_results: dict[str, Any] | None


class ScreenerResultsResponse(BaseModel):
    date: DateType
    config_id: int
    config_name: str
    rows: list[ScreenerResultRow]


@router.get("/configs", response_model=list[FilterConfigSummary])
def list_configs(active_only: bool = Query(default=False)) -> list[FilterConfigSummary]:
    with get_session() as session:
        stmt = select(FilterConfig).order_by(FilterConfig.id)
        if active_only:
            stmt = stmt.where(FilterConfig.is_active.is_(True))
        rows = session.execute(stmt).scalars().all()
        return [_summary_from_config(c) for c in rows]


@router.get("/configs/{config_id}", response_model=FilterConfigDetail)
def get_config(config_id: int) -> FilterConfigDetail:
    with get_session() as session:
        config = session.get(FilterConfig, config_id)
        if config is None:
            raise HTTPException(status_code=404, detail=f"config not found: {config_id}")
        summary = _summary_from_config(config)
        return FilterConfigDetail(
            **summary.model_dump(),
            config_json=config.config_json or {},
        )


_DATE_QUERY = Query(default=None, alias="date")


@router.get("/results", response_model=ScreenerResultsResponse)
def list_results(
    config_id: int | None = Query(default=None),
    as_of: DateType | None = _DATE_QUERY,
    passed_only: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=500),
) -> ScreenerResultsResponse:
    with get_session() as session:
        config = _resolve_config(session, config_id)
        target_date = as_of or _latest_date_for(session, config.id)
        if target_date is None:
            return ScreenerResultsResponse(
                date=as_of or DateType.today(),
                config_id=config.id,
                config_name=config.name,
                rows=[],
            )

        stmt = (
            select(ScreenerResult)
            .where(
                ScreenerResult.config_id == config.id,
                ScreenerResult.date == target_date,
            )
            .order_by(desc(ScreenerResult.score), ScreenerResult.symbol)
            .limit(limit)
        )
        if passed_only:
            stmt = stmt.where(ScreenerResult.passed.is_(True))
        rows = session.execute(stmt).scalars().all()
        if not rows:
            return ScreenerResultsResponse(
                date=target_date,
                config_id=config.id,
                config_name=config.name,
                rows=[],
            )

        symbols = [r.symbol for r in rows]
        tickers = _tickers_by_symbol(session, symbols)
        indicators = _indicators_by_symbol(session, symbols, target_date)
        next_earnings = _next_earnings_by_symbol(session, symbols, target_date)

        return ScreenerResultsResponse(
            date=target_date,
            config_id=config.id,
            config_name=config.name,
            rows=[
                _row_to_out(r, tickers.get(r.symbol), indicators.get(r.symbol), next_earnings)
                for r in rows
            ],
        )


@router.get("/results/{symbol}", response_model=list[ScreenerResultRow])
def symbol_history(
    symbol: str,
    config_id: int | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
) -> list[ScreenerResultRow]:
    sym = symbol.upper()
    cutoff = DateType.today() - timedelta(days=days)
    with get_session() as session:
        config = _resolve_config(session, config_id)
        rows = (
            session.execute(
                select(ScreenerResult)
                .where(
                    ScreenerResult.symbol == sym,
                    ScreenerResult.config_id == config.id,
                    ScreenerResult.date >= cutoff,
                )
                .order_by(ScreenerResult.date.desc())
            )
            .scalars()
            .all()
        )
        if not rows:
            return []
        ticker = session.get(Ticker, sym)
        # Indicator + earnings lookups are per-row but we only have one symbol;
        # fold them into a tiny per-row helper.
        return [_row_to_out(r, ticker, None, {}) for r in rows]


def _summary_from_config(config: FilterConfig) -> FilterConfigSummary:
    raw = config.config_json or {}
    filter_ids: list[str] = []
    for entry in raw.get("filters") or []:
        if isinstance(entry, dict):
            fid = entry.get("id")
            if isinstance(fid, str):
                filter_ids.append(fid)
    return FilterConfigSummary(
        id=config.id,
        name=config.name,
        description=config.description,
        is_active=config.is_active,
        filter_ids=filter_ids,
    )


def _resolve_config(session: Session, config_id: int | None) -> FilterConfig:
    if config_id is not None:
        config = session.get(FilterConfig, config_id)
        if config is None:
            raise HTTPException(status_code=404, detail=f"config not found: {config_id}")
        return config
    config = session.execute(
        select(FilterConfig)
        .where(FilterConfig.is_active.is_(True))
        .order_by(FilterConfig.id)
        .limit(1)
    ).scalar_one_or_none()
    if config is None:
        raise HTTPException(status_code=404, detail="no active configs")
    return config


def _latest_date_for(session: Session, config_id: int) -> DateType | None:
    result: DateType | None = session.execute(
        select(func.max(ScreenerResult.date)).where(ScreenerResult.config_id == config_id)
    ).scalar_one_or_none()
    return result


def _tickers_by_symbol(session: Session, symbols: list[str]) -> dict[str, Ticker]:
    rows = session.execute(select(Ticker).where(Ticker.symbol.in_(symbols))).scalars().all()
    return {t.symbol: t for t in rows}


def _indicators_by_symbol(
    session: Session, symbols: list[str], as_of: DateType
) -> dict[str, IndicatorDaily]:
    # Latest indicator row per symbol at-or-before as_of.
    subq = (
        select(IndicatorDaily.symbol, func.max(IndicatorDaily.date).label("max_date"))
        .where(IndicatorDaily.symbol.in_(symbols), IndicatorDaily.date <= as_of)
        .group_by(IndicatorDaily.symbol)
        .subquery()
    )
    rows = (
        session.execute(
            select(IndicatorDaily).join(
                subq,
                (IndicatorDaily.symbol == subq.c.symbol) & (IndicatorDaily.date == subq.c.max_date),
            )
        )
        .scalars()
        .all()
    )
    return {ind.symbol: ind for ind in rows}


def _next_earnings_by_symbol(
    session: Session, symbols: list[str], as_of: DateType
) -> dict[str, DateType]:
    rows = session.execute(
        select(Earnings.symbol, func.min(Earnings.earnings_date))
        .where(Earnings.symbol.in_(symbols), Earnings.earnings_date >= as_of)
        .group_by(Earnings.symbol)
    ).all()
    return {row[0]: row[1] for row in rows}


def _row_to_out(
    row: ScreenerResult,
    ticker: Ticker | None,
    indicator: IndicatorDaily | None,
    next_earnings: dict[str, DateType],
) -> ScreenerResultRow:
    near_200ema_pct = _extract_filter_value(row.filter_results_json, "near_200ema")
    return ScreenerResultRow(
        date=row.date,
        symbol=row.symbol,
        config_id=row.config_id,
        passed=row.passed,
        score=row.score,
        sector=ticker.sector if ticker else None,
        rsi_14=indicator.rsi_14 if indicator else None,
        iv_rank=indicator.iv_rank if indicator else None,
        iv_percentile=indicator.iv_percentile if indicator else None,
        near_200ema_pct=near_200ema_pct,
        next_earnings_date=next_earnings.get(row.symbol),
        filter_results=row.filter_results_json,
    )


def _extract_filter_value(
    filter_results_json: dict[str, Any] | None, filter_id: str
) -> float | None:
    if not filter_results_json:
        return None
    entry = filter_results_json.get(filter_id)
    if not isinstance(entry, dict):
        return None
    value = entry.get("value")
    if isinstance(value, int | float):
        return float(value)
    return None
