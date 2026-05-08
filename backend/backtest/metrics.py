"""Risk + return statistics for a strategy backtest run.

Pure functions over an equity series (``[(date, equity), ...]``) plus the
list of persisted ``BacktestTrade`` rows. Designed for the run-detail UI
(comparison page + per-run summary), so the inputs are intentionally
narrow — anything that needs more data should fetch it before calling.

Annualization uses 252 trading days; the equity series is already on a
trading-day cadence (one row per trading day) per ``simulator.py``.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as DateType

from db.models.backtest import BacktestTrade

# Cycle-state mirrors api/backtest.py — keep in sync if the simulator's
# leg vocabulary changes.
_CYCLE_TERMINATING_LEGS = {"csp_expired", "cc_assigned"}
_CC_LEGS = {"cc_open", "cc_close", "cc_assigned", "cc_expired"}
TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class StrategyMetrics:
    sharpe: float | None
    sortino: float | None
    max_drawdown_pct: float | None
    cagr: float | None
    win_rate: float | None
    avg_win: float | None
    avg_loss: float | None
    profit_factor: float | None
    expectancy: float | None
    cycles_completed: int
    assignment_rate: float | None
    avg_dte_held: float | None

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "max_drawdown_pct": self.max_drawdown_pct,
            "cagr": self.cagr,
            "win_rate": self.win_rate,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "profit_factor": self.profit_factor,
            "expectancy": self.expectancy,
            "cycles_completed": self.cycles_completed,
            "assignment_rate": self.assignment_rate,
            "avg_dte_held": self.avg_dte_held,
        }


def compute_strategy_metrics(
    *,
    equity_series: Sequence[tuple[DateType, float]],
    trades: Sequence[BacktestTrade],
    risk_free_rate: float = 0.0,
) -> StrategyMetrics:
    """Compute the full metric pack for one strategy run."""
    daily_returns = _daily_returns(equity_series)
    sharpe = _annualized_sharpe(daily_returns, risk_free_rate=risk_free_rate)
    sortino = _annualized_sortino(daily_returns, risk_free_rate=risk_free_rate)
    max_dd = _max_drawdown_pct(equity_series)
    cagr = _cagr(equity_series)

    closed = [t for t in trades if t.realized_pnl is not None]
    pnls: list[float] = [float(t.realized_pnl) for t in closed if t.realized_pnl is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    win_rate = (len(wins) / len(pnls)) if pnls else None
    avg_win = (sum(wins) / len(wins)) if wins else None
    avg_loss = (sum(losses) / len(losses)) if losses else None
    profit_factor: float | None
    if losses and sum(losses) != 0:
        profit_factor = sum(wins) / abs(sum(losses))
    elif wins:
        profit_factor = math.inf
    else:
        profit_factor = None
    expectancy = (sum(pnls) / len(pnls)) if pnls else None

    cycles_completed = _count_completed_cycles(trades)
    assignments = sum(1 for t in trades if t.leg_type == "cc_assigned")
    assignment_rate = (assignments / cycles_completed) if cycles_completed else None
    avg_dte_held = _avg_dte_held(closed)

    return StrategyMetrics(
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown_pct=max_dd,
        cagr=cagr,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        expectancy=expectancy,
        cycles_completed=cycles_completed,
        assignment_rate=assignment_rate,
        avg_dte_held=avg_dte_held,
    )


def drawdown_series(
    equity_series: Sequence[tuple[DateType, float]],
) -> list[tuple[DateType, float]]:
    """Per-day underwater curve as ``(date, drawdown_pct)``.

    Drawdown is reported as a non-positive percentage (0 at peak,
    -10.5 at 10.5% under). Empty input returns ``[]``.
    """
    if not equity_series:
        return []
    out: list[tuple[DateType, float]] = []
    peak = -math.inf
    for day, equity in equity_series:
        if equity > peak:
            peak = equity
        if peak <= 0:
            out.append((day, 0.0))
            continue
        out.append((day, (equity - peak) / peak * 100.0))
    return out


def _daily_returns(equity_series: Sequence[tuple[DateType, float]]) -> list[float]:
    if len(equity_series) < 2:
        return []
    out: list[float] = []
    prev = equity_series[0][1]
    for _, equity in equity_series[1:]:
        if prev <= 0:
            prev = equity
            continue
        out.append((equity - prev) / prev)
        prev = equity
    return out


def _annualized_sharpe(returns: list[float], *, risk_free_rate: float) -> float | None:
    if len(returns) < 2:
        return None
    daily_rf = risk_free_rate / TRADING_DAYS_PER_YEAR
    excess = [r - daily_rf for r in returns]
    sigma = statistics.pstdev(excess)
    if sigma == 0:
        return None
    return (statistics.fmean(excess) / sigma) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _annualized_sortino(returns: list[float], *, risk_free_rate: float) -> float | None:
    if len(returns) < 2:
        return None
    daily_rf = risk_free_rate / TRADING_DAYS_PER_YEAR
    excess = [r - daily_rf for r in returns]
    downside = [r for r in excess if r < 0]
    if not downside:
        return None
    # Population stdev of negative excess returns, anchored at zero.
    downside_sigma = math.sqrt(sum(r * r for r in downside) / len(downside))
    if downside_sigma == 0:
        return None
    return (statistics.fmean(excess) / downside_sigma) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _max_drawdown_pct(equity_series: Sequence[tuple[DateType, float]]) -> float | None:
    if not equity_series:
        return None
    peak = -math.inf
    worst = 0.0
    for _, equity in equity_series:
        if equity > peak:
            peak = equity
        if peak <= 0:
            continue
        dd = (equity - peak) / peak * 100.0
        if dd < worst:
            worst = dd
    return worst


def _cagr(equity_series: Sequence[tuple[DateType, float]]) -> float | None:
    if len(equity_series) < 2:
        return None
    start_eq = equity_series[0][1]
    end_eq = equity_series[-1][1]
    if start_eq <= 0:
        return None
    days = (equity_series[-1][0] - equity_series[0][0]).days
    if days <= 0:
        return None
    years = days / 365.25
    if years <= 0:
        return None
    growth = end_eq / start_eq
    if growth <= 0:
        return None
    return (growth ** (1.0 / years) - 1.0) * 100.0


def _count_completed_cycles(trades: Sequence[BacktestTrade]) -> int:
    """Mirror api/backtest.py's accounting from persisted trades."""
    closed: set[int] = set()
    csp_close_cycles: set[int] = set()
    cc_cycles: set[int] = set()
    for t in trades:
        if t.cycle_id is None:
            continue
        if t.leg_type in _CYCLE_TERMINATING_LEGS:
            closed.add(t.cycle_id)
        elif t.leg_type == "csp_close":
            csp_close_cycles.add(t.cycle_id)
        if t.leg_type in _CC_LEGS:
            cc_cycles.add(t.cycle_id)
    closed |= csp_close_cycles - cc_cycles
    return len(closed)


def _avg_dte_held(closed_trades: Sequence[BacktestTrade]) -> float | None:
    spans: list[int] = []
    for t in closed_trades:
        if t.entry_date is None or t.exit_date is None:
            continue
        spans.append((t.exit_date - t.entry_date).days)
    if not spans:
        return None
    return sum(spans) / len(spans)


__all__ = (
    "StrategyMetrics",
    "compute_strategy_metrics",
    "drawdown_series",
    "TRADING_DAYS_PER_YEAR",
)
