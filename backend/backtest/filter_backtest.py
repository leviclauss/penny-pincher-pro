"""Filter forward-return backtest.

Replays one screener config day-by-day across an NYSE trading-day calendar.
For each ``(symbol, day)`` pass, records a ``backtest_trades`` row with the
forward-return realized over ``forward_days`` trading days. The strategy
simulator (option pricing, equity curve, capital management) is deferred —
this is the candidate-quality eval only.

Symbols that need an options chain return ``ineligible`` from the relevant
filter and drop out cleanly; an unexpected exception while evaluating a
symbol is logged and skipped rather than aborting the run.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

import pandas_market_calendars as mcal
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.logging import get_logger
from db.models.backtest import BacktestRun, BacktestTrade
from db.models.market import Ticker
from db.models.screener import FilterConfig
from screener.context import build_context
from screener.pipeline import ParsedConfig, evaluate_symbol, parse_config

from .forward_returns import ForwardReturn, compute_forward_return_detail

log = get_logger(__name__)

DEFAULT_CALENDAR = "NYSE"
DEFAULT_STARTING_CAPITAL = 10_000.0
LEG_TYPE = "filter_pass"


@dataclass(slots=True)
class _DayStats:
    candidates: int = 0
    with_return: int = 0


@dataclass(slots=True)
class BacktestSummary:
    run_id: int
    days_evaluated: int = 0
    candidates: int = 0
    trades_written: int = 0
    returns: list[float] = field(default_factory=list)

    @property
    def mean_return(self) -> float | None:
        return sum(self.returns) / len(self.returns) if self.returns else None

    @property
    def median_return(self) -> float | None:
        if not self.returns:
            return None
        ordered = sorted(self.returns)
        n = len(ordered)
        mid = n // 2
        return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2

    @property
    def win_rate(self) -> float | None:
        if not self.returns:
            return None
        wins = sum(1 for r in self.returns if r > 0)
        return wins / len(self.returns)


def run_filter_backtest(
    session: Session,
    *,
    config_id: int,
    start_date: date,
    end_date: date,
    forward_days: int = 30,
    symbols: Sequence[str] | None = None,
    calendar_name: str = DEFAULT_CALENDAR,
) -> int:
    """Evaluate ``config_id`` on every trading day in ``[start_date, end_date]``.

    Returns the new ``backtest_runs.id``. Trade rows for each filter pass are
    written incrementally; the run row is written at the end so its ``params_json``
    can include the actual symbol universe used.
    """
    config_row = session.execute(
        select(FilterConfig).where(FilterConfig.id == config_id)
    ).scalar_one_or_none()
    if config_row is None:
        raise ValueError(f"unknown filter config id: {config_id}")
    parsed = parse_config(config_row)

    universe = _load_universe(session, symbols)
    if not universe:
        raise ValueError("no active tickers in the universe")

    trading_days = _trading_days(calendar_name, start_date, end_date)

    run = BacktestRun(
        config_id=config_id,
        start_date=start_date,
        end_date=end_date,
        starting_capital=DEFAULT_STARTING_CAPITAL,
        params_json={
            "forward_days": forward_days,
            "symbols": list(universe),
            "calendar": calendar_name,
        },
    )
    session.add(run)
    session.flush()  # populate run.id before writing trade rows
    run_id = run.id

    summary = BacktestSummary(run_id=run_id)
    for day in trading_days:
        stats = _evaluate_day(session, run_id, parsed, day, universe, forward_days, summary)
        summary.days_evaluated += 1
        log.info(
            "backtest.day.done",
            run_id=run_id,
            date=day.isoformat(),
            candidates=stats.candidates,
            trades=stats.with_return,
        )

    session.commit()
    log.info(
        "backtest.run.summary",
        run_id=run_id,
        days=summary.days_evaluated,
        candidates=summary.candidates,
        trades=summary.trades_written,
        mean_return=summary.mean_return,
        win_rate=summary.win_rate,
    )
    return run_id


def _evaluate_day(
    session: Session,
    run_id: int,
    config: ParsedConfig,
    day: date,
    universe: Sequence[str],
    forward_days: int,
    summary: BacktestSummary,
) -> _DayStats:
    stats = _DayStats()
    for symbol in universe:
        ctx = build_context(session, symbol, day, include_options=False)
        if ctx is None:
            continue
        try:
            result = evaluate_symbol(ctx, config)
        except Exception as exc:
            log.warning(
                "backtest.symbol.error",
                run_id=run_id,
                date=day.isoformat(),
                symbol=symbol,
                error=f"{type(exc).__name__}: {exc}",
            )
            continue
        if not result.passed:
            continue
        stats.candidates += 1
        summary.candidates += 1

        detail = compute_forward_return_detail(session, symbol, day, forward_days)
        if detail is None:
            continue
        _write_trade(session, run_id, symbol, day, detail)
        stats.with_return += 1
        summary.trades_written += 1
        summary.returns.append(detail.pct_return)
    return stats


def _write_trade(
    session: Session,
    run_id: int,
    symbol: str,
    entry_date: date,
    detail: ForwardReturn,
) -> None:
    pnl_pct = detail.pct_return * 100.0
    session.add(
        BacktestTrade(
            run_id=run_id,
            cycle_id=None,
            symbol=symbol,
            leg_type=LEG_TYPE,
            entry_date=entry_date,
            exit_date=detail.exit_date,
            entry_price=detail.entry_close,
            exit_price=detail.exit_close,
            outcome="win" if detail.pct_return > 0 else "loss",
            realized_pnl=pnl_pct,
            fees=0.0,
        )
    )


def _load_universe(session: Session, symbols: Sequence[str] | None) -> list[str]:
    stmt = select(Ticker.symbol).where(Ticker.is_active.is_(True), Ticker.is_hidden.is_(False))
    if symbols is not None:
        stmt = stmt.where(Ticker.symbol.in_({s.upper() for s in symbols}))
    return list(session.execute(stmt.order_by(Ticker.symbol)).scalars().all())


def _trading_days(calendar_name: str, start: date, end: date) -> list[date]:
    cal = mcal.get_calendar(calendar_name)
    schedule = cal.schedule(start_date=start, end_date=end)
    return [ts.date() for ts in schedule.index]
