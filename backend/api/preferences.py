"""Alert preferences resource — list + per-type upsert.

Backs the Settings page. Alert types are derived from the rendered
Telegram templates so the UI shows exactly the alerts the dispatcher
knows how to deliver. Preferences not yet persisted resolve to the
dispatcher's default (telegram, enabled, no quiet hours).
"""

from __future__ import annotations

from datetime import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from alerts.dispatcher import CHANNELS
from core.logging import get_logger
from db import get_session
from db.models.alerts import AlertPreference

log = get_logger(__name__)

router = APIRouter(prefix="/api/alerts/preferences", tags=["preferences"])

_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "alerts" / "templates" / "telegram"

_DEFAULT_CHANNELS = ["telegram"]


def _known_alert_types() -> list[str]:
    return sorted(p.stem.removesuffix(".md") for p in _TEMPLATE_DIR.glob("*.md.j2"))


class AlertPreferenceOut(BaseModel):
    alert_type: str
    channels: list[str]
    enabled: bool
    quiet_hours_start: str | None = Field(default=None, description="HH:MM, local time")
    quiet_hours_end: str | None = Field(default=None, description="HH:MM, local time")


class AlertPreferenceUpdate(BaseModel):
    channels: list[str]
    enabled: bool
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None


def _parse_hhmm(value: str | None, field: str) -> time | None:
    if value is None:
        return None
    try:
        hh, mm = value.split(":", 1)
        return time(int(hh), int(mm))
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422, detail=f"{field}: must be 'HH:MM', got {value!r}"
        ) from exc


def _format_hhmm(value: time | None) -> str | None:
    if value is None:
        return None
    return f"{value.hour:02d}:{value.minute:02d}"


def _to_out(alert_type: str, row: AlertPreference | None) -> AlertPreferenceOut:
    if row is None:
        return AlertPreferenceOut(
            alert_type=alert_type,
            channels=list(_DEFAULT_CHANNELS),
            enabled=True,
            quiet_hours_start=None,
            quiet_hours_end=None,
        )
    channels = [c for c in row.channels if isinstance(c, str)] if row.channels else []
    return AlertPreferenceOut(
        alert_type=row.alert_type,
        channels=channels or list(_DEFAULT_CHANNELS),
        enabled=row.enabled,
        quiet_hours_start=_format_hhmm(row.quiet_hours_start),
        quiet_hours_end=_format_hhmm(row.quiet_hours_end),
    )


@router.get("", response_model=list[AlertPreferenceOut])
def list_preferences() -> list[AlertPreferenceOut]:
    types = _known_alert_types()
    with get_session() as session:
        rows = (
            session.execute(
                select(AlertPreference).where(AlertPreference.alert_type.in_(types))
            )
            .scalars()
            .all()
        )
        by_type = {row.alert_type: row for row in rows}
    return [_to_out(t, by_type.get(t)) for t in types]


@router.put("/{alert_type}", response_model=AlertPreferenceOut)
def upsert_preference(alert_type: str, payload: AlertPreferenceUpdate) -> AlertPreferenceOut:
    unknown = [c for c in payload.channels if c not in CHANNELS]
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"unknown channel(s): {', '.join(sorted(unknown))}"
        )

    start = _parse_hhmm(payload.quiet_hours_start, "quiet_hours_start")
    end = _parse_hhmm(payload.quiet_hours_end, "quiet_hours_end")

    with get_session() as session:
        row = session.execute(
            select(AlertPreference).where(AlertPreference.alert_type == alert_type)
        ).scalar_one_or_none()
        if row is None:
            row = AlertPreference(
                alert_type=alert_type,
                channels=list(payload.channels),
                enabled=payload.enabled,
                quiet_hours_start=start,
                quiet_hours_end=end,
            )
            session.add(row)
        else:
            row.channels = list(payload.channels)
            row.enabled = payload.enabled
            row.quiet_hours_start = start
            row.quiet_hours_end = end
        session.commit()
        session.refresh(row)
        log.info(
            "preferences.upsert",
            alert_type=alert_type,
            channels=row.channels,
            enabled=row.enabled,
        )
        return _to_out(row.alert_type, row)
