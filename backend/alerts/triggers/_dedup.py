"""Dedup helpers shared across trigger families.

Trigger payloads land in ``alerts`` regardless of delivery outcome (the
dispatcher persists every fire). That table is the source of truth for
``already_dispatched_for_as_of`` so a re-run of the same job — manual
trigger plus the scheduled run, say — doesn't double-fire.

We match on ``payload_json.as_of`` (an ISO date string the digest builders
always include) rather than ``triggered_at``: it guarantees "one alert
per as-of date" even if dispatch happens at a wall-clock time that
doesn't share a calendar day with ``as_of`` (common in tests).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models.alerts import Alert


def already_dispatched_for_as_of(
    session: Session,
    alert_type: str,
    *,
    as_of: date,
    symbol: str | None = None,
) -> bool:
    """True if an ``alerts`` row exists for this type + ``as_of`` date.

    Matches on ``payload_json.as_of`` so trigger families that don't carry
    an ``as_of`` (e.g. position-management rules) are unaffected.
    """
    stmt = select(Alert.id).where(
        Alert.alert_type == alert_type,
        Alert.payload_json["as_of"].as_string() == as_of.isoformat(),
    )
    if symbol is not None:
        stmt = stmt.where(Alert.symbol == symbol)
    return session.execute(stmt.limit(1)).scalar_one_or_none() is not None


def already_dispatched_for_position_rule(
    session: Session,
    *,
    position_id: int,
    rule: str,
    alert_type: str = "position_management",
) -> bool:
    """True if a position alert has already fired for this (position, rule) pair.

    Each wheel cycle gets its own ``Position`` row (a new ``open_short_put``
    creates a new id) so matching on ``position_id`` alone gives us the
    "max 1 per condition per position lifecycle" rule from doc 03.
    """
    stmt = select(Alert.id).where(
        Alert.alert_type == alert_type,
        Alert.payload_json["position_id"].as_integer() == position_id,
        Alert.payload_json["rule"].as_string() == rule,
    )
    return session.execute(stmt.limit(1)).scalar_one_or_none() is not None
