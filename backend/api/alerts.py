"""Alerts router — history reads, ack toggle, and a manual test trigger.

The history routes back the ``/alerts`` page in the web UI: a chronological
feed of fired alerts (one row per dispatch) with optional filters by type,
symbol, or time window. Ack state is a single boolean on the row so the UI
can mark items as acted-on without an extra audit table.

The ``/test`` route remains for local end-to-end verification of channel
delivery — it loads a fixture payload and pushes it through the dispatcher
just like the scheduler jobs do.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import distinct, select

from alerts.channels.base import Channel
from alerts.dispatcher import CHANNELS, dispatch
from core.logging import get_logger
from db import get_session
from db.models.alerts import Alert

log = get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "backend" / "tests" / "fixtures" / "alerts"

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

# datetime defaults via Query(...) trip ruff B008 (datetime isn't in the
# immutable-types allowlist), so we hold them as module-level singletons.
_SINCE_QUERY = Query(default=None)
_UNTIL_QUERY = Query(default=None)


class AlertOut(BaseModel):
    id: int
    alert_type: str
    symbol: str | None
    config_id: int | None
    payload: dict[str, Any]
    triggered_at: datetime
    channels_sent: list[str]
    user_acked: bool


class AckIn(BaseModel):
    acked: bool


class AlertTestResponse(BaseModel):
    alert_type: str
    channel: str
    delivered: bool
    alert_id: int | None
    channels_attempted: list[str]
    channels_sent: list[str]
    skipped_reason: str | None


@router.get("", response_model=list[AlertOut])
def list_alerts(
    since: datetime | None = _SINCE_QUERY,
    until: datetime | None = _UNTIL_QUERY,
    alert_type: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[AlertOut]:
    stmt = select(Alert).order_by(Alert.triggered_at.desc())
    if since is not None:
        stmt = stmt.where(Alert.triggered_at >= since)
    if until is not None:
        stmt = stmt.where(Alert.triggered_at <= until)
    if alert_type:
        stmt = stmt.where(Alert.alert_type == alert_type)
    if symbol:
        stmt = stmt.where(Alert.symbol == symbol.upper())
    stmt = stmt.limit(limit).offset(offset)

    with get_session() as session:
        rows = session.execute(stmt).scalars().all()
        return [_to_out(row) for row in rows]


@router.get("/types", response_model=list[str])
def list_alert_types() -> list[str]:
    with get_session() as session:
        rows = session.execute(select(distinct(Alert.alert_type)).order_by(Alert.alert_type)).all()
        return [row[0] for row in rows if row[0]]


@router.post("/{alert_id}/ack", response_model=AlertOut)
def set_ack(alert_id: int, payload: AckIn) -> AlertOut:
    with get_session() as session:
        row = session.get(Alert, alert_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"alert not found: {alert_id}")
        row.user_acked = payload.acked
        session.commit()
        session.refresh(row)
        log.info("alerts.ack", alert_id=alert_id, acked=payload.acked)
        return _to_out(row)


@router.post("/test", response_model=AlertTestResponse)
def trigger_test_alert(
    channel: str = Query(default="telegram"),
    alert_type: str = Query(default="morning_digest"),
) -> AlertTestResponse:
    payload = _load_fixture(alert_type)
    channel_obj = CHANNELS.get(channel)
    if channel_obj is None:
        raise HTTPException(status_code=404, detail=f"unknown channel: {channel}")

    registry: dict[str, Channel] = {channel: channel_obj}
    result = dispatch(alert_type, payload, registry=registry)

    return AlertTestResponse(
        alert_type=alert_type,
        channel=channel,
        delivered=channel in result.channels_sent,
        alert_id=result.alert_id,
        channels_attempted=result.channels_attempted,
        channels_sent=result.channels_sent,
        skipped_reason=result.skipped_reason,
    )


def _to_out(row: Alert) -> AlertOut:
    channels: list[str] = []
    if row.channels_sent:
        try:
            decoded = json.loads(row.channels_sent)
        except json.JSONDecodeError:
            decoded = []
        if isinstance(decoded, list):
            channels = [c for c in decoded if isinstance(c, str)]
    return AlertOut(
        id=row.id,
        alert_type=row.alert_type,
        symbol=row.symbol,
        config_id=row.config_id,
        payload=row.payload_json or {},
        triggered_at=row.triggered_at,
        channels_sent=channels,
        user_acked=row.user_acked,
    )


def _load_fixture(alert_type: str) -> dict[str, Any]:
    path = FIXTURE_DIR / f"{alert_type}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no fixture for alert_type={alert_type}")
    with path.open() as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="fixture must be a JSON object")
    return data
