"""Coverage report for ``options_historical`` over a backtest window.

Used by the API to surface "real chain available?" before a strategy run
and by the UI to show users which dates / symbols would fall back to the
synthetic pricer if they kept ``use_real_chain=True``.

Pure read-only over ``options_historical`` + the trading-day calendar; no
side effects.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import pandas_market_calendars as mcal
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models.market import OptionsHistorical, Ticker


@dataclass(frozen=True)
class CoverageReport:
    start: date
    end: date
    calendar: str
    trading_days: int
    symbols_requested: list[str]
    symbols_with_any_data: list[str]
    symbols_missing: list[str]
    symbol_day_pairs_expected: int
    symbol_day_pairs_present: int
    first_uncovered_day: date | None

    @property
    def coverage_pct(self) -> float:
        if self.symbol_day_pairs_expected == 0:
            return 0.0
        return self.symbol_day_pairs_present / self.symbol_day_pairs_expected


def options_history_coverage(
    session: Session,
    *,
    start: date,
    end: date,
    symbols: list[str] | None = None,
    calendar: str = "NYSE",
) -> CoverageReport:
    """Summarize ``options_historical`` coverage over ``[start, end]``.

    Coverage is measured as ``(symbol, trading_day)`` pairs that have at
    least one row in ``options_historical``. Strike-window completeness
    isn't checked — the pricer falls back to synthetic per-row, so any
    row at all means the day is "real-chain reachable" for that symbol.
    """
    if end < start:
        raise ValueError("end must be on or after start")

    target = _resolve_symbols(session, symbols)
    days = _trading_days(calendar, start, end)
    days_set = set(days)

    if not target or not days:
        return CoverageReport(
            start=start,
            end=end,
            calendar=calendar,
            trading_days=len(days),
            symbols_requested=target,
            symbols_with_any_data=[],
            symbols_missing=list(target),
            symbol_day_pairs_expected=len(target) * len(days),
            symbol_day_pairs_present=0,
            first_uncovered_day=days[0] if days else None,
        )

    rows = session.execute(
        select(OptionsHistorical.symbol, OptionsHistorical.as_of)
        .where(OptionsHistorical.symbol.in_(target))
        .where(OptionsHistorical.as_of >= start)
        .where(OptionsHistorical.as_of <= end)
        .distinct()
    ).all()
    present_pairs: set[tuple[str, date]] = {(r[0], r[1]) for r in rows if r[1] in days_set}

    symbols_with_data: set[str] = {sym for sym, _ in present_pairs}
    symbols_missing = [s for s in target if s not in symbols_with_data]

    expected = len(target) * len(days)
    first_uncovered: date | None = None
    for day in days:
        for sym in target:
            if (sym, day) not in present_pairs:
                first_uncovered = day
                break
        if first_uncovered is not None:
            break

    return CoverageReport(
        start=start,
        end=end,
        calendar=calendar,
        trading_days=len(days),
        symbols_requested=target,
        symbols_with_any_data=sorted(symbols_with_data),
        symbols_missing=symbols_missing,
        symbol_day_pairs_expected=expected,
        symbol_day_pairs_present=len(present_pairs),
        first_uncovered_day=first_uncovered,
    )


def _resolve_symbols(session: Session, symbols: list[str] | None) -> list[str]:
    if symbols:
        return sorted({s.strip().upper() for s in symbols if s.strip()})
    rows = session.execute(
        select(Ticker.symbol).where(Ticker.is_active.is_(True)).order_by(Ticker.symbol)
    ).all()
    return [r[0] for r in rows]


def _trading_days(calendar_name: str, start: date, end: date) -> list[date]:
    cal = mcal.get_calendar(calendar_name)
    schedule = cal.schedule(start_date=start, end_date=end)
    return [ts.date() for ts in schedule.index]


__all__: Iterable[str] = ("CoverageReport", "options_history_coverage")
