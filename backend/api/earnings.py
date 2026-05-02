"""Earnings calendar slices used by the dashboard."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from db import get_session
from db.models.market import Earnings, Ticker

router = APIRouter(prefix="/api/earnings", tags=["earnings"])


class UpcomingEarning(BaseModel):
    symbol: str
    name: str | None
    earnings_date: date
    time_of_day: str | None


@router.get("/upcoming", response_model=list[UpcomingEarning])
def upcoming(days: int = Query(default=7, ge=1, le=90)) -> list[UpcomingEarning]:
    """Earnings for active tickers within the next ``days`` days."""
    today = date.today()
    end = today + timedelta(days=days)
    if end < today:
        raise HTTPException(status_code=400, detail="invalid window")
    with get_session() as session:
        rows = session.execute(
            select(
                Earnings.symbol,
                Earnings.earnings_date,
                Earnings.time_of_day,
                Ticker.name,
            )
            .join(Ticker, Ticker.symbol == Earnings.symbol)
            .where(
                Ticker.is_active.is_(True),
                Earnings.earnings_date >= today,
                Earnings.earnings_date <= end,
            )
            .order_by(Earnings.earnings_date, Earnings.symbol)
        ).all()
    return [
        UpcomingEarning(
            symbol=r.symbol,
            name=r.name,
            earnings_date=r.earnings_date,
            time_of_day=r.time_of_day,
        )
        for r in rows
    ]
