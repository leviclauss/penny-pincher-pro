"""Backtesting: filter forward-return analysis and full strategy simulation.

Two entry points:

- ``run_filter_backtest`` — replays a screener config day-by-day and records
  the realized forward-N-day return per ``(symbol, day)`` pass.
- ``run_strategy_backtest`` — full wheel simulator (cash-secured put →
  covered call), with synthetic Black-Scholes pricing, capital management,
  and equity-curve writes. See ``simulator.py`` and ``pricing.py``.
"""

from .filter_backtest import BacktestSummary, run_filter_backtest
from .forward_returns import ForwardReturn, compute_forward_return
from .simulator import StrategyParams, StrategyRunSummary, run_strategy_backtest

__all__ = [
    "BacktestSummary",
    "ForwardReturn",
    "StrategyParams",
    "StrategyRunSummary",
    "compute_forward_return",
    "run_filter_backtest",
    "run_strategy_backtest",
]
