"""Stale-data guard for alerts.

Digests are useless — and misleading — if the underlying data is stale
because ingestion failed. Each trigger checks freshness via
``latest_bar_date`` and bails (with a logged reason) when the most recent
bar predates the threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models.market import BarDaily


@dataclass(frozen=True, slots=True)
class FreshnessCheck:
    fresh: bool
    latest_bar_date: date | None
    max_age_days: int


def check_bar_freshness(
    session: Session,
    *,
    today: date,
    max_age_days: int = 4,
) -> FreshnessCheck:
    """True when the most recent bar in the DB is within ``max_age_days`` of ``today``.

    The default tolerates a long weekend (Fri close → Mon morning digest).
    Holidays widen the gap further; a 4-day tolerance covers Mon holidays
    after a Fri close without flagging a healthy DB as stale.
    """
    latest = session.execute(select(func.max(BarDaily.date))).scalar()
    if latest is None:
        return FreshnessCheck(fresh=False, latest_bar_date=None, max_age_days=max_age_days)
    age_days = (today - latest).days
    return FreshnessCheck(
        fresh=age_days <= max_age_days,
        latest_bar_date=latest,
        max_age_days=max_age_days,
    )
