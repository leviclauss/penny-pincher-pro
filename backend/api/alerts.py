"""Alerts router — manual test triggers and (later) history reads.

Phase 1 only ships ``POST /api/system/alerts/test`` so we can verify the
Telegram bot end-to-end before the screener pipeline emits real digests.
The route loads a canned payload from ``tests/fixtures/alerts/`` and runs it
through the dispatcher, which both sends to the channel and writes an
``alerts`` row.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from alerts.channels.base import Channel
from alerts.dispatcher import CHANNELS, dispatch

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "backend" / "tests" / "fixtures" / "alerts"

router = APIRouter(prefix="/api/system/alerts", tags=["alerts"])


class AlertTestResponse(BaseModel):
    alert_type: str
    channel: str
    delivered: bool
    alert_id: int | None
    channels_attempted: list[str]
    channels_sent: list[str]
    skipped_reason: str | None


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


def _load_fixture(alert_type: str) -> dict[str, Any]:
    path = FIXTURE_DIR / f"{alert_type}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no fixture for alert_type={alert_type}")
    with path.open() as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="fixture must be a JSON object")
    return data
