"""FastAPI application entry point.

Skeleton only — domain routes (screener, configs, positions, etc.) are added
in later sessions. The health endpoint is enough to:
- verify the backend is up from the frontend
- give CI/uptime monitors something to ping
- surface last-bar freshness once ingestion has populated the DB
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import func, select

from api import macro as macro_router
from api import tickers as tickers_router
from core.config import get_settings
from core.logging import configure_logging, get_logger
from core.time import utcnow
from db import get_session
from db.models.market import BarDaily

log = get_logger(__name__)


class HealthStatus(BaseModel):
    status: str
    app_env: str
    server_time_utc: str
    database_url_scheme: str
    last_bar_date: date | None
    bar_count: int


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)
    log.info("api.startup", env=settings.app_env)
    yield
    log.info("api.shutdown")


app = FastAPI(title="Penny Pincher Pro", version="0.1.0", lifespan=lifespan)

app.include_router(tickers_router.router)
app.include_router(macro_router.router)


@app.get("/api/system/health", response_model=HealthStatus)
def health() -> HealthStatus:
    settings = get_settings()
    with get_session() as session:
        last_bar = session.execute(select(func.max(BarDaily.date))).scalar_one_or_none()
        bar_count = session.execute(select(func.count()).select_from(BarDaily)).scalar_one()

    return HealthStatus(
        status="ok",
        app_env=settings.app_env,
        server_time_utc=utcnow().isoformat(),
        database_url_scheme=settings.database_url.split(":", 1)[0],
        last_bar_date=last_bar,
        bar_count=int(bar_count),
    )
