"""Portfolios resource: lightweight tags for grouping positions.

Portfolios have no capital tracking — they exist purely so the user can
slice positions by container ("IRA", "Taxable", etc.). A position may
belong to at most one portfolio; ``portfolio_id`` is nullable.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from core.logging import get_logger
from db import get_session
from db.models.positions import Portfolio, Position

log = get_logger(__name__)

router = APIRouter(prefix="/api/portfolios", tags=["portfolios"])


class PortfolioOut(BaseModel):
    id: int
    name: str
    created_at: datetime
    position_count: int


class PortfolioCreateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


@router.get("", response_model=list[PortfolioOut])
def list_portfolios() -> list[PortfolioOut]:
    with get_session() as session:
        rows = session.execute(
            select(Portfolio, func.count(Position.id))
            .join(Position, Position.portfolio_id == Portfolio.id, isouter=True)
            .group_by(Portfolio.id)
            .order_by(Portfolio.name)
        ).all()
    return [
        PortfolioOut(id=p.id, name=p.name, created_at=p.created_at, position_count=int(n))
        for p, n in rows
    ]


@router.post("", response_model=PortfolioOut, status_code=201)
def create_portfolio(body: PortfolioCreateBody) -> PortfolioOut:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name cannot be blank")
    try:
        with get_session() as session:
            portfolio = Portfolio(name=name)
            session.add(portfolio)
            session.flush()
            out = PortfolioOut(
                id=portfolio.id,
                name=portfolio.name,
                created_at=portfolio.created_at,
                position_count=0,
            )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail=f"portfolio '{name}' already exists") from exc
    log.info("portfolios.create", id=out.id, name=out.name)
    return out


@router.delete("/{portfolio_id}", status_code=204)
def delete_portfolio(portfolio_id: int) -> None:
    with get_session() as session:
        portfolio = session.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise HTTPException(status_code=404, detail=f"portfolio {portfolio_id} not found")
        session.delete(portfolio)
    log.info("portfolios.delete", id=portfolio_id)
