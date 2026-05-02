"""Macro context: VIX + SPY regime, current snapshot and history."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from db import get_session
from db.models.market import MacroDaily

router = APIRouter(prefix="/api/macro", tags=["macro"])


_RANGE_TO_DAYS: dict[str, int] = {
    "1m": 31,
    "3m": 93,
    "6m": 186,
    "1y": 372,
    "2y": 744,
}


class MacroPoint(BaseModel):
    date: date
    vix_close: float | None
    vix_9d: float | None
    vix_term_structure: float | None
    spy_close: float | None
    spy_ema_200: float | None
    spy_above_200ema: bool | None


@router.get("/current", response_model=MacroPoint | None)
def current() -> MacroPoint | None:
    """Most recent macro snapshot, or null if the table is empty."""
    with get_session() as session:
        row = session.execute(
            select(MacroDaily).order_by(MacroDaily.date.desc()).limit(1)
        ).scalar_one_or_none()
    if row is None:
        return None
    return MacroPoint(
        date=row.date,
        vix_close=row.vix_close,
        vix_9d=row.vix_9d,
        vix_term_structure=row.vix_term_structure,
        spy_close=row.spy_close,
        spy_ema_200=row.spy_ema_200,
        spy_above_200ema=row.spy_above_200ema,
    )


@router.get("/history", response_model=list[MacroPoint])
def history(range: str = Query(default="6m")) -> list[MacroPoint]:
    days = _RANGE_TO_DAYS.get(range.lower())
    if days is None:
        raise HTTPException(status_code=400, detail=f"unsupported range: {range}")
    cutoff = date.today() - timedelta(days=days)
    with get_session() as session:
        rows = (
            session.execute(
                select(MacroDaily).where(MacroDaily.date >= cutoff).order_by(MacroDaily.date)
            )
            .scalars()
            .all()
        )
    return [
        MacroPoint(
            date=r.date,
            vix_close=r.vix_close,
            vix_9d=r.vix_9d,
            vix_term_structure=r.vix_term_structure,
            spy_close=r.spy_close,
            spy_ema_200=r.spy_ema_200,
            spy_above_200ema=r.spy_above_200ema,
        )
        for r in rows
    ]
