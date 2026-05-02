"""Ticker resource: list, daily chart series, IV history."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from core.logging import get_logger
from db import get_session
from db.models.market import BarDaily, Earnings, IndicatorDaily, Ticker

log = get_logger(__name__)

router = APIRouter(prefix="/api/tickers", tags=["tickers"])


_RANGE_TO_DAYS: dict[str, int] = {
    "1m": 31,
    "3m": 93,
    "6m": 186,
    "1y": 372,
    "2y": 744,
    "5y": 1860,
    "max": 36500,
}


def _parse_range(range_: str) -> int:
    days = _RANGE_TO_DAYS.get(range_.lower())
    if days is None:
        raise HTTPException(status_code=400, detail=f"unsupported range: {range_}")
    return days


class TickerSummary(BaseModel):
    symbol: str
    name: str | None
    tier: int | None
    sector: str | None
    market_cap: float | None
    is_active: bool
    last_close: float | None
    last_close_date: date | None
    ema_200: float | None
    rsi_14: float | None
    iv_atm: float | None
    next_earnings_date: date | None


class ChartBar(BaseModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    ema_20: float | None
    ema_50: float | None
    ema_200: float | None
    rsi_14: float | None


class IVPoint(BaseModel):
    date: date
    iv_atm: float | None
    iv_rank: float | None
    iv_percentile: float | None


@router.get("", response_model=list[TickerSummary])
def list_tickers() -> list[TickerSummary]:
    """Return every ticker with its latest bar + indicator + next earnings."""
    today = date.today()
    with get_session() as session:
        tickers = session.execute(select(Ticker).order_by(Ticker.symbol)).scalars().all()

        latest_bar_date_subq = (
            select(BarDaily.symbol, func.max(BarDaily.date).label("max_date"))
            .group_by(BarDaily.symbol)
            .subquery()
        )
        latest_bars = (
            session.execute(
                select(BarDaily).join(
                    latest_bar_date_subq,
                    (BarDaily.symbol == latest_bar_date_subq.c.symbol)
                    & (BarDaily.date == latest_bar_date_subq.c.max_date),
                )
            )
            .scalars()
            .all()
        )
        bars_by_symbol = {b.symbol: b for b in latest_bars}

        # Indicator row keyed to each symbol's latest bar date, NOT the latest
        # indicator date — the IV pass writes IV-only rows on non-trading days
        # that have no ema/rsi.
        latest_inds = (
            session.execute(
                select(IndicatorDaily).join(
                    latest_bar_date_subq,
                    (IndicatorDaily.symbol == latest_bar_date_subq.c.symbol)
                    & (IndicatorDaily.date == latest_bar_date_subq.c.max_date),
                )
            )
            .scalars()
            .all()
        )
        inds_by_symbol = {i.symbol: i for i in latest_inds}

        next_earnings_subq = (
            select(Earnings.symbol, func.min(Earnings.earnings_date).label("next_date"))
            .where(Earnings.earnings_date >= today)
            .group_by(Earnings.symbol)
            .subquery()
        )
        next_earnings_rows = session.execute(
            select(next_earnings_subq.c.symbol, next_earnings_subq.c.next_date)
        ).all()
        next_earnings_by_symbol: dict[str, date] = {
            row.symbol: row.next_date for row in next_earnings_rows
        }

        out: list[TickerSummary] = []
        for t in tickers:
            bar = bars_by_symbol.get(t.symbol)
            ind = inds_by_symbol.get(t.symbol)
            out.append(
                TickerSummary(
                    symbol=t.symbol,
                    name=t.name,
                    tier=t.tier,
                    sector=t.sector,
                    market_cap=t.market_cap,
                    is_active=t.is_active,
                    last_close=bar.close if bar else None,
                    last_close_date=bar.date if bar else None,
                    ema_200=ind.ema_200 if ind else None,
                    rsi_14=ind.rsi_14 if ind else None,
                    iv_atm=ind.iv_atm if ind else None,
                    next_earnings_date=next_earnings_by_symbol.get(t.symbol),
                )
            )
    return out


@router.get("/{symbol}/chart", response_model=list[ChartBar])
def chart(symbol: str, range: str = Query(default="1y")) -> list[ChartBar]:
    sym = symbol.upper()
    days = _parse_range(range)
    cutoff = date.today() - timedelta(days=days)
    with get_session() as session:
        ticker = session.get(Ticker, sym)
        if ticker is None:
            raise HTTPException(status_code=404, detail=f"ticker not found: {sym}")

        rows = session.execute(
            select(BarDaily, IndicatorDaily)
            .join(
                IndicatorDaily,
                (BarDaily.symbol == IndicatorDaily.symbol) & (BarDaily.date == IndicatorDaily.date),
                isouter=True,
            )
            .where(BarDaily.symbol == sym, BarDaily.date >= cutoff)
            .order_by(BarDaily.date)
        ).all()

    return [
        ChartBar(
            date=bar.date,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            ema_20=ind.ema_20 if ind else None,
            ema_50=ind.ema_50 if ind else None,
            ema_200=ind.ema_200 if ind else None,
            rsi_14=ind.rsi_14 if ind else None,
        )
        for bar, ind in rows
    ]


@router.get("/{symbol}/iv-history", response_model=list[IVPoint])
def iv_history(symbol: str, range: str = Query(default="1y")) -> list[IVPoint]:
    sym = symbol.upper()
    days = _parse_range(range)
    cutoff = date.today() - timedelta(days=days)
    with get_session() as session:
        ticker = session.get(Ticker, sym)
        if ticker is None:
            raise HTTPException(status_code=404, detail=f"ticker not found: {sym}")

        rows = (
            session.execute(
                select(IndicatorDaily)
                .where(IndicatorDaily.symbol == sym, IndicatorDaily.date >= cutoff)
                .order_by(IndicatorDaily.date)
            )
            .scalars()
            .all()
        )

    return [
        IVPoint(
            date=r.date,
            iv_atm=r.iv_atm,
            iv_rank=r.iv_rank,
            iv_percentile=r.iv_percentile,
        )
        for r in rows
    ]
