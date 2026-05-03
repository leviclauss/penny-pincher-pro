"""Backtesting: filter forward-return analysis and full strategy simulation.

v0 ships the filter forward-return backtest only — see
``backend/backtest/filter_backtest.py``. The full strategy simulator (option
pricing, equity curve, capital management) is deferred.
"""

from .filter_backtest import BacktestSummary, run_filter_backtest
from .forward_returns import ForwardReturn, compute_forward_return

__all__ = [
    "BacktestSummary",
    "ForwardReturn",
    "compute_forward_return",
    "run_filter_backtest",
]
