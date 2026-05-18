"""Ticker resource: list, daily chart series, IV history, watchlist mutations."""

from __future__ import annotations

import re
import threading
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from core.logging import get_logger
from core.time import utcnow
from db import get_session
from db.models.market import BarDaily, Earnings, IndicatorDaily, OptionsSnapshot, Ticker
from ingestion.ticker_backfill import run_ticker_backfill

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

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")


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
    is_hidden: bool
    ticker_source: str
    last_close: float | None
    last_close_date: date | None
    ema_200: float | None
    rsi_14: float | None
    iv_atm: float | None
    next_earnings_date: date | None


class TickerCreate(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16)
    name: str | None = None
    tier: int | None = None
    notes: str | None = None


class TickerPatch(BaseModel):
    is_hidden: bool | None = None
    tier: int | None = None
    notes: str | None = None


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


def _summary_from_row(
    t: Ticker,
    bar: BarDaily | None,
    ind: IndicatorDaily | None,
    next_earnings: date | None,
) -> TickerSummary:
    return TickerSummary(
        symbol=t.symbol,
        name=t.name,
        tier=t.tier,
        sector=t.sector,
        market_cap=t.market_cap,
        is_active=t.is_active,
        is_hidden=t.is_hidden,
        ticker_source=t.ticker_source,
        last_close=bar.close if bar else None,
        last_close_date=bar.date if bar else None,
        ema_200=ind.ema_200 if ind else None,
        rsi_14=ind.rsi_14 if ind else None,
        iv_atm=ind.iv_atm if ind else None,
        next_earnings_date=next_earnings,
    )


@router.get("/sectors", response_model=list[str])
def list_sectors() -> list[str]:
    """Distinct non-null sectors across every ticker (active + hidden).

    Drives the sector filter chips on the Tickers page and the
    ``sector_allowed`` filter's multi-select in the screener-config editor.
    """
    with get_session() as session:
        rows = session.execute(
            select(Ticker.sector)
            .where(Ticker.sector.is_not(None))
            .distinct()
            .order_by(Ticker.sector)
        ).all()
        return [row[0] for row in rows if row[0]]


@router.get("", response_model=list[TickerSummary])
def list_tickers(
    include_hidden: bool = Query(default=False),
) -> list[TickerSummary]:
    """Return tickers with their latest bar + indicator + next earnings.

    Hidden tickers are excluded by default; pass ``include_hidden=true`` to
    include them (used by the dashboard count and the "Show hidden" toggle).
    """
    today = date.today()
    with get_session() as session:
        stmt = select(Ticker).order_by(Ticker.symbol)
        if not include_hidden:
            stmt = stmt.where(Ticker.is_hidden.is_(False))
        tickers = session.execute(stmt).scalars().all()

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

        return [
            _summary_from_row(
                t,
                bars_by_symbol.get(t.symbol),
                inds_by_symbol.get(t.symbol),
                next_earnings_by_symbol.get(t.symbol),
            )
            for t in tickers
        ]


@router.post("", response_model=TickerSummary, status_code=201)
def create_ticker(
    payload: TickerCreate,
    background: BackgroundTasks,
) -> TickerSummary:
    """Add a ticker to the watchlist and trigger an async backfill."""
    sym = payload.symbol.strip().upper()
    if not _SYMBOL_RE.match(sym):
        raise HTTPException(status_code=422, detail=f"invalid symbol: {sym}")

    with get_session() as session:
        existing = session.get(Ticker, sym)
        if existing is not None:
            if existing.ticker_source == "watchlist":
                raise HTTPException(status_code=409, detail=f"ticker exists: {sym}")
            # Promote universe ticker to watchlist: make it visible and user-managed.
            existing.ticker_source = "watchlist"
            existing.is_hidden = False
            if payload.tier is not None:
                existing.tier = payload.tier
            if payload.name:
                existing.name = payload.name
            if payload.notes is not None:
                existing.notes = payload.notes
            existing.updated_at = utcnow()
            session.commit()
            session.refresh(existing)
            summary = _summary_from_row(existing, None, None, None)
            log.info("tickers.promote_from_universe", symbol=sym)
            return summary

        now = utcnow()
        ticker = Ticker(
            symbol=sym,
            name=payload.name,
            tier=payload.tier,
            notes=payload.notes,
            is_active=True,
            is_hidden=False,
            ticker_source="watchlist",
            added_at=now,
            updated_at=now,
        )
        session.add(ticker)
        session.commit()
        session.refresh(ticker)
        summary = _summary_from_row(ticker, None, None, None)

    background.add_task(_run_in_thread, lambda: run_ticker_backfill(sym))
    log.info("tickers.create", symbol=sym)
    return summary


@router.patch("/{symbol}", response_model=TickerSummary)
def patch_ticker(symbol: str, payload: TickerPatch) -> TickerSummary:
    sym = symbol.upper()
    today = date.today()
    with get_session() as session:
        ticker = session.get(Ticker, sym)
        if ticker is None:
            raise HTTPException(status_code=404, detail=f"ticker not found: {sym}")

        fields_set = payload.model_fields_set
        if "is_hidden" in fields_set and payload.is_hidden is not None:
            ticker.is_hidden = payload.is_hidden
        if "tier" in fields_set:
            ticker.tier = payload.tier
        if "notes" in fields_set:
            ticker.notes = payload.notes
        ticker.updated_at = utcnow()
        session.commit()
        session.refresh(ticker)

        bar = session.execute(
            select(BarDaily).where(BarDaily.symbol == sym).order_by(BarDaily.date.desc()).limit(1)
        ).scalar_one_or_none()
        ind = (
            session.execute(
                select(IndicatorDaily).where(
                    IndicatorDaily.symbol == sym, IndicatorDaily.date == bar.date
                )
            ).scalar_one_or_none()
            if bar is not None
            else None
        )
        next_er = session.execute(
            select(func.min(Earnings.earnings_date)).where(
                Earnings.symbol == sym, Earnings.earnings_date >= today
            )
        ).scalar_one_or_none()

        log.info("tickers.patch", symbol=sym, is_hidden=ticker.is_hidden, tier=ticker.tier)
        return _summary_from_row(ticker, bar, ind, next_er)


@router.delete("/{symbol}", status_code=204)
def delete_ticker(symbol: str) -> Response:
    sym = symbol.upper()
    with get_session() as session:
        ticker = session.get(Ticker, sym)
        if ticker is None:
            raise HTTPException(status_code=404, detail=f"ticker not found: {sym}")

        session.execute(delete(IndicatorDaily).where(IndicatorDaily.symbol == sym))
        session.execute(delete(OptionsSnapshot).where(OptionsSnapshot.symbol == sym))
        session.execute(delete(Earnings).where(Earnings.symbol == sym))
        session.execute(delete(BarDaily).where(BarDaily.symbol == sym))
        session.delete(ticker)
        session.commit()
        log.info("tickers.delete", symbol=sym)

    return Response(status_code=204)


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


def _run_in_thread(body: Any) -> None:
    """Run the job body off the event loop so it doesn't block FastAPI."""
    threading.Thread(target=body, daemon=True).start()
