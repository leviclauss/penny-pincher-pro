"""Management-rule evaluator for open wheel positions.

Runs against the latest snapshot for each open position and yields a list of
``Trigger`` records — one per rule that fired. The scheduler job feeds those
into ``alerts.dispatcher.dispatch`` so each fires through whatever channels
the user has configured.

Defaults match doc 04 (50% max profit, 21 DTE, delta > 0.45, within 2% of
strike, CC ITM at ≤7 DTE, position open >60 days). All thresholds are
overridable via ``ManagementConfig`` so the UI/config layer can expose them
later without touching the rule code.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

import alerts.dispatcher as dispatcher_module
from alerts.triggers._dedup import already_dispatched_for_position_rule
from core.logging import get_logger
from core.time import utcnow
from db.models.positions import Position, PositionLeg, PositionSnapshot
from positions.state_machine import (
    LEG_COVERED_CALL,
    LEG_SHORT_PUT,
    OUTCOME_OPEN,
    STATE_COVERED_CALL,
    STATE_SHORT_PUT,
)

log = get_logger(__name__)

ALERT_TYPE = "position_management"


@dataclass(frozen=True, slots=True)
class ManagementConfig:
    pct_max_profit_threshold: float = 0.50
    dte_threshold: int = 21
    delta_threshold: float = 0.45
    near_strike_pct: float = 0.02  # within 2%
    cc_itm_dte_threshold: int = 7
    stale_days: int = 60


@dataclass(frozen=True, slots=True)
class Trigger:
    rule: str
    position_id: int
    symbol: str
    payload: dict[str, Any] = field(default_factory=dict)


def evaluate_position(
    *,
    position: Position,
    leg: PositionLeg | None,
    snapshot: PositionSnapshot | None,
    today: date,
    config: ManagementConfig,
) -> list[Trigger]:
    """Pure rule evaluation for a single position.

    Returns one trigger per rule that fired. ``leg`` is the open option leg
    (if any) and ``snapshot`` is the latest position_snapshots row.
    """
    triggers: list[Trigger] = []

    if position.opened_at is not None:
        days_open = (today - position.opened_at.date()).days
        if days_open >= config.stale_days:
            triggers.append(
                Trigger(
                    rule="stale_position",
                    position_id=position.id,
                    symbol=position.symbol,
                    payload={"days_open": days_open, "threshold": config.stale_days},
                )
            )

    if leg is None or snapshot is None:
        return triggers

    if (
        snapshot.pct_max_profit is not None
        and snapshot.pct_max_profit >= config.pct_max_profit_threshold
    ):
        triggers.append(
            Trigger(
                rule="pct_max_profit",
                position_id=position.id,
                symbol=position.symbol,
                payload={
                    "pct_max_profit": snapshot.pct_max_profit,
                    "threshold": config.pct_max_profit_threshold,
                },
            )
        )

    if snapshot.dte is not None and snapshot.dte <= config.dte_threshold:
        triggers.append(
            Trigger(
                rule="dte",
                position_id=position.id,
                symbol=position.symbol,
                payload={"dte": snapshot.dte, "threshold": config.dte_threshold},
            )
        )

    if (
        position.state == STATE_SHORT_PUT
        and snapshot.delta is not None
        and abs(snapshot.delta) >= config.delta_threshold
    ):
        triggers.append(
            Trigger(
                rule="delta_breach",
                position_id=position.id,
                symbol=position.symbol,
                payload={"delta": snapshot.delta, "threshold": config.delta_threshold},
            )
        )

    if snapshot.underlying_price is not None and leg.strike is not None and leg.strike > 0:
        diff_pct = abs(snapshot.underlying_price - leg.strike) / leg.strike
        if diff_pct <= config.near_strike_pct:
            triggers.append(
                Trigger(
                    rule="near_strike",
                    position_id=position.id,
                    symbol=position.symbol,
                    payload={
                        "underlying": snapshot.underlying_price,
                        "strike": leg.strike,
                        "diff_pct": diff_pct,
                        "threshold": config.near_strike_pct,
                    },
                )
            )

    if (
        position.state == STATE_COVERED_CALL
        and snapshot.dte is not None
        and snapshot.dte <= config.cc_itm_dte_threshold
        and snapshot.underlying_price is not None
        and leg.strike is not None
        and snapshot.underlying_price >= leg.strike
    ):
        triggers.append(
            Trigger(
                rule="cc_itm_short_dte",
                position_id=position.id,
                symbol=position.symbol,
                payload={
                    "dte": snapshot.dte,
                    "underlying": snapshot.underlying_price,
                    "strike": leg.strike,
                },
            )
        )

    return triggers


def run_management_pass(
    session: Session,
    *,
    config: ManagementConfig | None = None,
    today: date | None = None,
) -> list[Trigger]:
    """Evaluate every open position and return the full trigger list."""
    cfg = config or ManagementConfig()
    as_of = today or utcnow().date()

    positions = (
        session.execute(
            select(Position).where(Position.state.in_((STATE_SHORT_PUT, STATE_COVERED_CALL)))
        )
        .scalars()
        .all()
    )

    triggers: list[Trigger] = []
    for position in positions:
        leg = _open_option_leg(session, position)
        snapshot = _latest_snapshot(session, position.id)
        triggers.extend(
            evaluate_position(
                position=position,
                leg=leg,
                snapshot=snapshot,
                today=as_of,
                config=cfg,
            )
        )

    log.info("positions.management.done", triggers=len(triggers))
    return triggers


def trigger_to_payload(trigger: Trigger) -> dict[str, Any]:
    """Build the dispatch payload for ``alerts.dispatcher.dispatch``."""
    return {
        "rule": trigger.rule,
        "position_id": trigger.position_id,
        "symbol": trigger.symbol,
        **trigger.payload,
    }


@dataclass(frozen=True, slots=True)
class FireResult:
    dispatched: int
    suppressed: int


def fire_triggers(session: Session, triggers: Sequence[Trigger]) -> FireResult:
    """Best-effort fan-out to the alert dispatcher with per-rule lifecycle dedup.

    Suppresses any (position_id, rule) pair that has already produced an
    ``alerts`` row — implements doc 03's "max 1 per condition per position
    lifecycle" rule.
    """
    if not triggers:
        return FireResult(dispatched=0, suppressed=0)

    dispatched = 0
    suppressed = 0
    for trigger in triggers:
        if already_dispatched_for_position_rule(
            session,
            position_id=trigger.position_id,
            rule=trigger.rule,
            alert_type=ALERT_TYPE,
        ):
            log.info(
                "positions.management.suppressed",
                rule=trigger.rule,
                position_id=trigger.position_id,
            )
            suppressed += 1
            continue
        try:
            dispatcher_module.dispatch(ALERT_TYPE, trigger_to_payload(trigger))
        except Exception as exc:  # pragma: no cover — dispatcher already best-effort
            log.warning(
                "positions.management.dispatch_failed",
                rule=trigger.rule,
                position_id=trigger.position_id,
                error=str(exc),
            )
        else:
            dispatched += 1
    return FireResult(dispatched=dispatched, suppressed=suppressed)


def _open_option_leg(session: Session, position: Position) -> PositionLeg | None:
    leg_type = LEG_SHORT_PUT if position.state == STATE_SHORT_PUT else LEG_COVERED_CALL
    return session.execute(
        select(PositionLeg).where(
            PositionLeg.position_id == position.id,
            PositionLeg.leg_type == leg_type,
            PositionLeg.outcome == OUTCOME_OPEN,
        )
    ).scalar_one_or_none()


def _latest_snapshot(session: Session, position_id: int) -> PositionSnapshot | None:
    return session.execute(
        select(PositionSnapshot)
        .where(PositionSnapshot.position_id == position_id)
        .order_by(PositionSnapshot.snapshot_at.desc())
        .limit(1)
    ).scalar_one_or_none()
