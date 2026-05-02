"""Management-rule evaluator coverage.

Each test isolates a single rule by constructing the position + leg + latest
snapshot ad hoc and asserting that exactly the expected trigger rules fire.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from db.models.positions import Position, PositionLeg, PositionSnapshot
from positions import state_machine as sm
from positions.management import (
    ManagementConfig,
    Trigger,
    evaluate_position,
    trigger_to_payload,
)


def _make_position(state: str = sm.STATE_SHORT_PUT, days_open: int = 5) -> Position:
    opened = datetime(2026, 5, 1, tzinfo=UTC)
    return Position(
        id=1,
        symbol="AAPL",
        state=state,
        cycle_id=1,
        opened_at=opened,
        closed_at=None,
        notes=None,
    )


def _make_leg(
    *,
    leg_type: str = sm.LEG_SHORT_PUT,
    strike: float = 170.0,
    contracts: int = 1,
    entry_price: float = 3.0,
) -> PositionLeg:
    return PositionLeg(
        id=10,
        position_id=1,
        leg_type=leg_type,
        symbol="AAPL",
        expiration=date(2026, 6, 19),
        strike=strike,
        contracts=contracts,
        entry_price=entry_price,
        outcome=sm.OUTCOME_OPEN,
        fees=0.0,
    )


def _make_snapshot(
    *,
    pct_max_profit: float | None = None,
    dte: int | None = None,
    delta: float | None = None,
    underlying: float | None = None,
) -> PositionSnapshot:
    return PositionSnapshot(
        position_id=1,
        snapshot_at=datetime(2026, 5, 6, tzinfo=UTC),
        underlying_price=underlying,
        option_mid=None,
        unrealized_pnl=None,
        pct_max_profit=pct_max_profit,
        delta=delta,
        dte=dte,
    )


def _rules(triggers: list[Trigger]) -> set[str]:
    return {t.rule for t in triggers}


def test_pct_max_profit_fires_when_threshold_hit() -> None:
    triggers = evaluate_position(
        position=_make_position(),
        leg=_make_leg(),
        snapshot=_make_snapshot(pct_max_profit=0.55),
        today=date(2026, 5, 6),
        config=ManagementConfig(),
    )
    assert "pct_max_profit" in _rules(triggers)


def test_pct_max_profit_quiet_when_below() -> None:
    triggers = evaluate_position(
        position=_make_position(),
        leg=_make_leg(),
        snapshot=_make_snapshot(pct_max_profit=0.30),
        today=date(2026, 5, 6),
        config=ManagementConfig(),
    )
    assert "pct_max_profit" not in _rules(triggers)


def test_dte_threshold_fires_at_or_below() -> None:
    triggers = evaluate_position(
        position=_make_position(),
        leg=_make_leg(),
        snapshot=_make_snapshot(dte=21),
        today=date(2026, 5, 6),
        config=ManagementConfig(),
    )
    assert "dte" in _rules(triggers)


def test_delta_breach_only_for_short_put() -> None:
    triggers_put = evaluate_position(
        position=_make_position(state=sm.STATE_SHORT_PUT),
        leg=_make_leg(),
        snapshot=_make_snapshot(delta=-0.50),
        today=date(2026, 5, 6),
        config=ManagementConfig(),
    )
    triggers_cc = evaluate_position(
        position=_make_position(state=sm.STATE_COVERED_CALL),
        leg=_make_leg(leg_type=sm.LEG_COVERED_CALL),
        snapshot=_make_snapshot(delta=0.50),
        today=date(2026, 5, 6),
        config=ManagementConfig(),
    )
    assert "delta_breach" in _rules(triggers_put)
    assert "delta_breach" not in _rules(triggers_cc)


def test_near_strike_within_threshold() -> None:
    triggers = evaluate_position(
        position=_make_position(),
        leg=_make_leg(strike=170.0),
        snapshot=_make_snapshot(underlying=171.5),  # 0.88% above strike
        today=date(2026, 5, 6),
        config=ManagementConfig(),
    )
    assert "near_strike" in _rules(triggers)


def test_near_strike_outside_threshold() -> None:
    triggers = evaluate_position(
        position=_make_position(),
        leg=_make_leg(strike=170.0),
        snapshot=_make_snapshot(underlying=180.0),
        today=date(2026, 5, 6),
        config=ManagementConfig(),
    )
    assert "near_strike" not in _rules(triggers)


def test_cc_itm_short_dte_fires_when_itm_and_close_to_expiry() -> None:
    triggers = evaluate_position(
        position=_make_position(state=sm.STATE_COVERED_CALL),
        leg=_make_leg(leg_type=sm.LEG_COVERED_CALL, strike=175.0),
        snapshot=_make_snapshot(dte=5, underlying=176.0),
        today=date(2026, 5, 6),
        config=ManagementConfig(),
    )
    assert "cc_itm_short_dte" in _rules(triggers)


def test_cc_itm_quiet_when_otm() -> None:
    triggers = evaluate_position(
        position=_make_position(state=sm.STATE_COVERED_CALL),
        leg=_make_leg(leg_type=sm.LEG_COVERED_CALL, strike=175.0),
        snapshot=_make_snapshot(dte=5, underlying=170.0),
        today=date(2026, 5, 6),
        config=ManagementConfig(),
    )
    assert "cc_itm_short_dte" not in _rules(triggers)


def test_stale_position_fires_after_threshold_days() -> None:
    triggers = evaluate_position(
        position=_make_position(),
        leg=None,
        snapshot=None,
        today=date(2026, 7, 5),  # 65 days after 2026-05-01
        config=ManagementConfig(),
    )
    assert "stale_position" in _rules(triggers)


def test_no_open_leg_or_snapshot_skips_option_rules() -> None:
    triggers = evaluate_position(
        position=_make_position(),
        leg=None,
        snapshot=None,
        today=date(2026, 5, 6),
        config=ManagementConfig(),
    )
    assert _rules(triggers) == set()


def test_trigger_payload_includes_core_fields() -> None:
    payload = trigger_to_payload(
        Trigger(rule="dte", position_id=7, symbol="AAPL", payload={"dte": 21, "threshold": 21})
    )
    assert payload["rule"] == "dte"
    assert payload["position_id"] == 7
    assert payload["symbol"] == "AAPL"
    assert payload["dte"] == 21
