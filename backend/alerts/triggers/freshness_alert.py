"""Data freshness alert trigger.

Fires after the evening pipeline when symbols have stale or missing data.
Uses the existing dispatcher + dedup infrastructure.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.logging import get_logger
from db.models.market import BarDaily, Ticker

log = get_logger(__name__)

ALERT_TYPE = "data_freshness_warning"
DEFAULT_MAX_AGE_DAYS = 3


def build_freshness_alert_payload(
    session: Session,
    *,
    as_of: date,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    symbols_skipped: list[str] | None = None,
) -> dict[str, Any] | None:
    """Build payload for stale-data alert. Returns None if everything is fresh."""
    active_symbols: list[str] = list(
        session.execute(
            select(Ticker.symbol).where(Ticker.is_active.is_(True)).order_by(Ticker.symbol)
        )
        .scalars()
        .all()
    )

    # Latest bar date per active symbol.
    latest_bars: dict[str, date] = dict(
        session.execute(
            select(BarDaily.symbol, func.max(BarDaily.date))
            .where(BarDaily.symbol.in_(active_symbols))
            .group_by(BarDaily.symbol)
        ).all()
    )

    stale_symbols: list[dict[str, Any]] = []
    for symbol in active_symbols:
        last_bar_date = latest_bars.get(symbol)
        if last_bar_date is None:
            continue
        days_stale = (as_of - last_bar_date).days
        if days_stale > max_age_days:
            stale_symbols.append(
                {
                    "symbol": symbol,
                    "last_bar_date": last_bar_date.isoformat(),
                    "days_stale": days_stale,
                }
            )

    skipped = symbols_skipped or []

    if not stale_symbols and not skipped:
        log.info("freshness_alert.all_fresh", active=len(active_symbols))
        return None

    log.info(
        "freshness_alert.building",
        stale=len(stale_symbols),
        skipped=len(skipped),
        active=len(active_symbols),
    )

    return {
        "as_of": as_of.isoformat(),
        "stale_symbols": stale_symbols,
        "skipped_symbols": skipped,
        "total_active": len(active_symbols),
        "stale_count": len(stale_symbols),
        "skipped_count": len(skipped),
    }
