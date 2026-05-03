"""FastAPI application entry point.

Domain routes live in per-resource routers (``api/system.py``,
``api/tickers.py``, etc.) and are included here. The lifespan handler boots
the structured logger and, when ``SCHEDULER_ENABLED``, the APScheduler.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI

from api import alerts as alerts_router
from api import backtest as backtest_router
from api import earnings as earnings_router
from api import macro as macro_router
from api import positions as positions_router
from api import preferences as preferences_router
from api import screener as screener_router
from api import tickers as tickers_router
from api.system import router as system_router
from core.config import get_settings
from core.logging import configure_logging, get_logger
from scheduler.app import create_and_start, shutdown

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)
    log.info("api.startup", env=settings.app_env)

    scheduler: BackgroundScheduler | None = None
    if settings.scheduler_enabled:
        scheduler = create_and_start()
        cast(dict[str, object], app.state.__dict__)["scheduler"] = scheduler
    else:
        log.info("api.startup.scheduler_disabled")

    try:
        yield
    finally:
        if scheduler is not None:
            shutdown(scheduler)
        log.info("api.shutdown")


app = FastAPI(title="Penny Pincher Pro", version="0.1.0", lifespan=lifespan)
app.include_router(system_router)
app.include_router(backtest_router.router)
app.include_router(tickers_router.router)
app.include_router(macro_router.router)
app.include_router(earnings_router.router)
app.include_router(screener_router.router)
app.include_router(alerts_router.router)
app.include_router(positions_router.router)
app.include_router(preferences_router.router)
