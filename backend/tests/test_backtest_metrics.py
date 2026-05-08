"""Tests for ``backtest.metrics``.

Verifies:
- Empty / degenerate inputs return None instead of NaN/Inf.
- Sharpe + Sortino track expected sign/magnitude on hand-built series.
- Max drawdown is the deepest peak-to-trough.
- Cycles + assignment rate count correctly across leg types.
- Drawdown series produces a 0-anchored underwater curve.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from backtest.metrics import (
    compute_strategy_metrics,
    drawdown_series,
)
from db.models.backtest import BacktestTrade


def _trade(
    *,
    leg_type: str,
    cycle_id: int | None = 1,
    realized_pnl: float | None = None,
    entry_date: date | None = None,
    exit_date: date | None = None,
) -> BacktestTrade:
    """Build a not-yet-persisted BacktestTrade with safe defaults."""
    return BacktestTrade(
        run_id=1,
        cycle_id=cycle_id,
        symbol="AAPL",
        leg_type=leg_type,
        entry_date=entry_date or date(2024, 1, 2),
        exit_date=exit_date,
        strike=170.0,
        expiration=date(2024, 1, 26),
        entry_price=2.0,
        exit_price=1.0,
        realized_pnl=realized_pnl,
        fees=0.0,
    )


def _equity_series(values: list[float]) -> list[tuple[date, float]]:
    start = date(2024, 1, 2)
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


def test_empty_inputs_return_nones() -> None:
    metrics = compute_strategy_metrics(equity_series=[], trades=[])
    assert metrics.sharpe is None
    assert metrics.sortino is None
    assert metrics.max_drawdown_pct is None
    assert metrics.cagr is None
    assert metrics.win_rate is None
    assert metrics.cycles_completed == 0
    assert metrics.assignment_rate is None


def test_flat_equity_no_returns_no_sharpe() -> None:
    metrics = compute_strategy_metrics(
        equity_series=_equity_series([100, 100, 100, 100]),
        trades=[],
    )
    assert metrics.sharpe is None
    assert metrics.sortino is None  # no downside
    # Flat curve never goes underwater.
    assert metrics.max_drawdown_pct == 0.0


def test_drawdown_tracks_peak_to_trough() -> None:
    metrics = compute_strategy_metrics(
        equity_series=_equity_series([100, 110, 99, 105, 80, 90]),
        trades=[],
    )
    # Peak = 110, trough = 80 → -27.27%.
    assert metrics.max_drawdown_pct is not None
    assert math.isclose(metrics.max_drawdown_pct, (80 - 110) / 110 * 100, rel_tol=1e-6)


def test_sharpe_positive_for_drift_up() -> None:
    metrics = compute_strategy_metrics(
        equity_series=_equity_series([100, 101, 102, 103, 104, 105, 106, 107, 108]),
        trades=[],
    )
    assert metrics.sharpe is not None
    assert metrics.sharpe > 0


def test_sortino_none_when_no_downside() -> None:
    metrics = compute_strategy_metrics(
        equity_series=_equity_series([100, 101, 102, 103, 104]),
        trades=[],
    )
    # Strictly increasing series → no downside returns to anchor sortino on.
    assert metrics.sortino is None


def test_win_rate_and_profit_factor() -> None:
    trades = [
        _trade(leg_type="csp_expired", cycle_id=1, realized_pnl=100.0),
        _trade(leg_type="csp_expired", cycle_id=2, realized_pnl=200.0),
        _trade(leg_type="cc_assigned", cycle_id=3, realized_pnl=-150.0),
    ]
    metrics = compute_strategy_metrics(
        equity_series=_equity_series([10000, 10100, 10300, 10150]),
        trades=trades,
    )
    assert metrics.win_rate == 2 / 3
    assert metrics.avg_win == 150.0
    assert metrics.avg_loss == -150.0
    # Profit factor: 300 wins / 150 losses = 2.0
    assert metrics.profit_factor == 2.0
    assert metrics.expectancy == (100 + 200 - 150) / 3


def test_profit_factor_inf_when_no_losses() -> None:
    trades = [
        _trade(leg_type="csp_expired", cycle_id=1, realized_pnl=10.0),
    ]
    metrics = compute_strategy_metrics(
        equity_series=_equity_series([100, 110]),
        trades=trades,
    )
    assert metrics.profit_factor == math.inf


def test_cycles_completed_and_assignment_rate() -> None:
    # Three closed cycles, one ended via cc_assigned.
    trades = [
        # Cycle 1: csp_open + csp_expired (closed)
        _trade(leg_type="csp_open", cycle_id=1),
        _trade(leg_type="csp_expired", cycle_id=1, realized_pnl=10.0),
        # Cycle 2: csp_open + cc_open + cc_assigned (closed via assignment)
        _trade(leg_type="csp_open", cycle_id=2),
        _trade(leg_type="cc_open", cycle_id=2),
        _trade(leg_type="cc_assigned", cycle_id=2, realized_pnl=-5.0),
        # Cycle 3: csp_open + csp_close (no cc), counts as closed
        _trade(leg_type="csp_open", cycle_id=3),
        _trade(leg_type="csp_close", cycle_id=3, realized_pnl=15.0),
        # Cycle 4: csp_open only (still open) — must NOT count
        _trade(leg_type="csp_open", cycle_id=4),
    ]
    metrics = compute_strategy_metrics(
        equity_series=_equity_series([100, 100, 100]),
        trades=trades,
    )
    assert metrics.cycles_completed == 3
    assert metrics.assignment_rate == 1 / 3


def test_avg_dte_held_only_counts_closed_trades() -> None:
    trades = [
        _trade(
            leg_type="csp_expired",
            entry_date=date(2024, 1, 1),
            exit_date=date(2024, 1, 11),
            realized_pnl=5.0,
        ),
        _trade(
            leg_type="csp_expired",
            entry_date=date(2024, 1, 5),
            exit_date=date(2024, 1, 25),
            realized_pnl=5.0,
        ),
        # Open trade — exit_date None, must not skew the average.
        _trade(leg_type="csp_open", entry_date=date(2024, 1, 10)),
    ]
    metrics = compute_strategy_metrics(
        equity_series=_equity_series([100, 105, 110]),
        trades=trades,
    )
    assert metrics.avg_dte_held == (10 + 20) / 2


def test_drawdown_series_anchors_at_zero() -> None:
    series = drawdown_series(_equity_series([100, 110, 99, 121]))
    # First sample is the seed peak → 0%. Day 1 sets a new peak at 110 → 0%.
    # Day 2 is at 99 vs 110 peak → ~-10%. Day 3 sets a new peak at 121 → 0%.
    assert series[0][1] == 0.0
    assert series[1][1] == 0.0
    assert math.isclose(series[2][1], (99 - 110) / 110 * 100, rel_tol=1e-6)
    assert series[3][1] == 0.0


def test_drawdown_series_empty() -> None:
    assert drawdown_series([]) == []
