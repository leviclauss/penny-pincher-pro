"""Morning + evening digest builders.

Pure payload builders: each ``build_*`` function reads from the DB and
returns the dict the matching Telegram template expects. The scheduler
job wraps the builder in ``alerts.dispatcher.dispatch`` plus dedup +
freshness guards.

Design notes:
- Morning digest runs *before* the day's screener pass (it's a wakeup
  brief), so "screener hits" reflect the latest run on or before
  ``as_of`` — typically yesterday's evening pipeline.
- Evening digest runs *after* the screener pass on the same calendar
  day, so "screener hits" reflect today's run.
- Position attention summarizes management triggers without firing them
  individually — that fan-out belongs to the position-management trigger
  (phase 2).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.logging import get_logger
from db.models.market import BarDaily, Earnings, IndicatorDaily, MacroDaily
from db.models.positions import Position, PositionLeg, PositionSnapshot
from db.models.screener import FilterConfig, ScreenerResult
from positions.management import (
    ManagementConfig,
    Trigger,
    evaluate_position,
)
from positions.state_machine import (
    LEG_COVERED_CALL,
    LEG_SHORT_PUT,
    OUTCOME_OPEN,
    STATE_COVERED_CALL,
    STATE_SHORT_PUT,
)

log = get_logger(__name__)

MORNING_DIGEST = "morning_digest"
EVENING_DIGEST = "evening_digest"

_MAX_HITS = 20  # cap so a noisy day doesn't blow past Telegram's 4096 char limit


def build_morning_digest_payload(session: Session, *, as_of: date) -> dict[str, Any]:
    """Wakeup brief: macro, latest screener hits, today's earnings, positions to watch."""
    macro = _macro_snapshot(session)
    hits = _screener_hits(session, as_of=as_of)
    earnings_today = _earnings_on(session, day=as_of)
    attention = _positions_attention(session, today=as_of)
    return {
        "as_of": as_of.isoformat(),
        "macro": macro,
        "screener_hits": hits,
        "earnings_today": earnings_today,
        "positions_attention": attention,
    }


def build_evening_digest_payload(session: Session, *, as_of: date) -> dict[str, Any]:
    """Post-close brief: macro, today's screener hits, P&L, tomorrow's earnings."""
    macro = _macro_snapshot(session)
    hits = _screener_hits(session, as_of=as_of)
    positions_pnl = _positions_pnl(session)
    earnings_tomorrow = _earnings_on(session, day=as_of + timedelta(days=1))
    return {
        "as_of": as_of.isoformat(),
        "macro": macro,
        "screener_hits": hits,
        "positions": positions_pnl,
        "earnings_tomorrow": earnings_tomorrow,
    }


def _macro_snapshot(session: Session) -> dict[str, Any]:
    row = session.execute(
        select(MacroDaily).order_by(MacroDaily.date.desc()).limit(1)
    ).scalar_one_or_none()
    if row is None:
        return {"vix": 0.0, "term": 1.0, "spy_above_200ema": False}
    return {
        "vix": float(row.vix_close) if row.vix_close is not None else 0.0,
        "term": float(row.vix_term_structure) if row.vix_term_structure is not None else 1.0,
        "spy_above_200ema": bool(row.spy_above_200ema),
    }


def _screener_hits(session: Session, *, as_of: date) -> list[dict[str, Any]]:
    latest = session.execute(
        select(func.max(ScreenerResult.date)).where(
            ScreenerResult.date <= as_of,
            ScreenerResult.passed.is_(True),
        )
    ).scalar()
    if latest is None:
        return []

    rows = session.execute(
        select(ScreenerResult, FilterConfig.name)
        .join(FilterConfig, FilterConfig.id == ScreenerResult.config_id)
        .where(
            ScreenerResult.date == latest,
            ScreenerResult.passed.is_(True),
        )
        .order_by(ScreenerResult.score.desc().nulls_last(), ScreenerResult.symbol)
    ).all()

    if not rows:
        return []

    symbols = {r[0].symbol for r in rows}

    indicators_by_symbol = {
        ind.symbol: ind
        for ind in session.execute(
            select(IndicatorDaily).where(
                IndicatorDaily.symbol.in_(symbols),
                IndicatorDaily.date == latest,
            )
        ).scalars()
    }

    bars_by_symbol = {
        bar.symbol: bar
        for bar in session.execute(
            select(BarDaily).where(
                BarDaily.symbol.in_(symbols),
                BarDaily.date == latest,
            )
        ).scalars()
    }

    next_earnings: dict[str, date] = {}
    earnings_rows = session.execute(
        select(Earnings.symbol, func.min(Earnings.earnings_date))
        .where(Earnings.symbol.in_(symbols), Earnings.earnings_date >= as_of)
        .group_by(Earnings.symbol)
    ).all()
    for symbol, earnings_date in earnings_rows:
        next_earnings[symbol] = earnings_date

    hits: list[dict[str, Any]] = []
    for result, config_name in rows[:_MAX_HITS]:
        bar = bars_by_symbol.get(result.symbol)
        ind = indicators_by_symbol.get(result.symbol)
        next_e = next_earnings.get(result.symbol)
        hits.append(
            {
                "symbol": result.symbol,
                "config": config_name,
                "close": float(bar.close) if bar is not None else 0.0,
                "rsi": _fmt_number(ind.rsi_14 if ind else None, digits=0),
                "ivp": _fmt_number(ind.iv_percentile if ind else None, digits=0),
                "score": float(result.score) if result.score is not None else 0.0,
                "next_earnings_days": (next_e - as_of).days if next_e else "—",
            }
        )
    return hits


def _earnings_on(session: Session, *, day: date) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Earnings.symbol, Earnings.time_of_day)
        .where(Earnings.earnings_date == day)
        .order_by(Earnings.symbol)
    ).all()
    return [{"symbol": symbol, "when": time_of_day or "—"} for symbol, time_of_day in rows]


def _positions_attention(session: Session, *, today: date) -> list[dict[str, Any]]:
    """One row per open position with at least one management trigger firing.

    Reuses ``positions.management.evaluate_position`` so the rules stay in
    one place. The note is a comma-joined list of fired rules, matching
    the existing morning digest template's free-form ``note`` field.
    """
    positions = (
        session.execute(
            select(Position).where(Position.state.in_((STATE_SHORT_PUT, STATE_COVERED_CALL)))
        )
        .scalars()
        .all()
    )

    if not positions:
        return []

    cfg = ManagementConfig()
    grouped: dict[int, list[Trigger]] = defaultdict(list)
    symbol_by_position: dict[int, str] = {}

    for position in positions:
        leg = _open_option_leg(session, position)
        snapshot = _latest_snapshot(session, position.id)
        triggers = evaluate_position(
            position=position,
            leg=leg,
            snapshot=snapshot,
            today=today,
            config=cfg,
        )
        if triggers:
            grouped[position.id].extend(triggers)
            symbol_by_position[position.id] = position.symbol

    return [
        {
            "position_id": pid,
            "symbol": symbol_by_position[pid],
            "note": ", ".join(_humanize(t) for t in triggers),
        }
        for pid, triggers in grouped.items()
    ]


def _positions_pnl(session: Session) -> list[dict[str, Any]]:
    positions = (
        session.execute(
            select(Position).where(Position.state.in_((STATE_SHORT_PUT, STATE_COVERED_CALL)))
        )
        .scalars()
        .all()
    )
    if not positions:
        return []

    rows: list[dict[str, Any]] = []
    for position in positions:
        snapshot = _latest_snapshot(session, position.id)
        rows.append(
            {
                "position_id": position.id,
                "symbol": position.symbol,
                "state": position.state,
                "unrealized_pnl": (
                    float(snapshot.unrealized_pnl)
                    if snapshot and snapshot.unrealized_pnl is not None
                    else 0.0
                ),
                "pct_max_profit": (
                    float(snapshot.pct_max_profit)
                    if snapshot and snapshot.pct_max_profit is not None
                    else 0.0
                ),
                "dte": snapshot.dte if snapshot and snapshot.dte is not None else "—",
            }
        )
    return rows


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


_RULE_LABELS = {
    "pct_max_profit": "≥50% profit",
    "dte": "≤21 DTE",
    "delta_breach": "delta breach",
    "near_strike": "near strike",
    "cc_itm_short_dte": "CC ITM near expiry",
    "stale_position": "stale (>60d)",
}


def _humanize(trigger: Trigger) -> str:
    return _RULE_LABELS.get(trigger.rule, trigger.rule)


def _fmt_number(value: float | None, *, digits: int) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"
