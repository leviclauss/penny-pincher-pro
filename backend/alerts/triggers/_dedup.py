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


def already_dispatched_for_symbol_on(
    session: Session,
    alert_type: str,
    *,
    as_of: date,
    symbol: str,
) -> bool:
    """True if an alert of this type already fired for ``symbol`` on ``as_of``.

    Used by intraday triggers (``setup_triggered``, ``iv_spike``) to enforce
    "max 1 per ticker per day" — a re-fire from a later poll within the same
    trading day is suppressed regardless of which config triggered it.
    """
    stmt = select(Alert.id).where(
        Alert.alert_type == alert_type,
        Alert.symbol == symbol,
        Alert.payload_json["as_of"].as_string() == as_of.isoformat(),
    )
    return session.execute(stmt.limit(1)).scalar_one_or_none() is not None


def already_dispatched_for_job_today(
    session: Session,
    *,
    job_name: str,
    today: date,
    alert_type: str = "job_failed",
) -> bool:
    """True if a ``job_failed`` alert already fired for ``job_name`` on ``today``.

    Limits failure alerts to one per (job_name, day) so a transient outage
    that fails the same scheduled job repeatedly through the day doesn't
    generate a Telegram fire-hose. The dedup window resets at midnight UTC.
    """
    stmt = select(Alert.id).where(
        Alert.alert_type == alert_type,
        Alert.payload_json["job_name"].as_string() == job_name,
        Alert.payload_json["as_of"].as_string() == today.isoformat(),
    )
    return session.execute(stmt.limit(1)).scalar_one_or_none() is not None


def symbol_in_morning_digest(
    session: Session,
    *,
    as_of: date,
    symbol: str,
) -> bool:
    """True if ``symbol`` appears in today's morning_digest ``screener_hits``.

    Backs the "intraday setup_triggered is suppressed if ticker already in
    morning summary" rule from doc 03. Returns False if no morning digest
    has fired yet today.
    """
    digest = session.execute(
        select(Alert.payload_json).where(
            Alert.alert_type == "morning_digest",
            Alert.payload_json["as_of"].as_string() == as_of.isoformat(),
        )
    ).scalar_one_or_none()
    if digest is None:
        return False
    hits = digest.get("screener_hits") if isinstance(digest, dict) else None
    if not isinstance(hits, list):
        return False
    return any(isinstance(h, dict) and h.get("symbol") == symbol for h in hits)
