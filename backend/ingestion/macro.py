"""Macro indicator fetcher: VIX, VIX9D, term structure, SPY 200 EMA regime.

Composes data from two sources:
- Yahoo Finance for ``^VIX`` and ``^VIX9D`` daily closes (Alpaca's index
  coverage is spotty; Yahoo is unauthenticated and reliable enough for EOD).
- ``bars_daily`` + ``indicators_daily`` for SPY (already populated by the
  bars/indicators pass — the macro step must run after those).

Term structure is ``vix_9d / vix_close`` (< 1 = backwardation per doc 01).
SPY regime ``spy_above_200ema`` is derived once here so the dashboard and
filter pipeline don't have to recompute it.

Each row in ``macro_daily`` is keyed by date. Re-running on the same window
upserts (overwrites) — values can revise as Yahoo recalculates.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.config import get_settings
from core.logging import get_logger
from db.models.market import BarDaily, IndicatorDaily, MacroDaily
from ingestion.yahoo_client import IndexBarRecord

log = get_logger(__name__)

VIX_SYMBOL = "^VIX"
VIX9D_SYMBOL = "^VIX9D"


class IndexHistorySource(Protocol):
    def get_index_history(
        self,
        symbol: str,
        *,
        days_back: int,
        as_of: date | None = ...,
    ) -> list[IndexBarRecord]: ...


@dataclass
class MacroFetchSummary:
    rows_written: int
    earliest: date | None
    latest: date | None


def fetch_macro(
    session: Session,
    client: IndexHistorySource,
    *,
    lookback_days: int | None = None,
    as_of: date | None = None,
    spy_symbol: str | None = None,
) -> MacroFetchSummary:
    """Fetch VIX/VIX9D + read SPY locally, upsert ``macro_daily`` rows."""
    settings = get_settings()
    lookback = lookback_days if lookback_days is not None else settings.macro_lookback_days
    spy = spy_symbol or settings.spy_symbol

    vix = _by_date(client.get_index_history(VIX_SYMBOL, days_back=lookback, as_of=as_of))
    vix9d = _by_date(client.get_index_history(VIX9D_SYMBOL, days_back=lookback, as_of=as_of))
    spy_close = _spy_close_by_date(session, spy, lookback)
    spy_ema = _spy_ema_by_date(session, spy, lookback)

    all_dates = sorted(set(vix) | set(vix9d) | set(spy_close))
    rows = [_build_row(d, vix, vix9d, spy_close, spy_ema) for d in all_dates]
    if not rows:
        log.info("macro.fetch.empty")
        return MacroFetchSummary(rows_written=0, earliest=None, latest=None)

    written = _upsert(session, rows)
    session.commit()

    earliest = all_dates[0]
    latest = all_dates[-1]
    log.info("macro.fetch.done", rows=written, earliest=str(earliest), latest=str(latest))
    return MacroFetchSummary(rows_written=written, earliest=earliest, latest=latest)


def _build_row(
    d: date,
    vix: dict[date, float],
    vix9d: dict[date, float],
    spy_close: dict[date, float],
    spy_ema: dict[date, float],
) -> dict[str, object]:
    vc = vix.get(d)
    v9 = vix9d.get(d)
    term = (v9 / vc) if (vc is not None and v9 is not None and vc > 0) else None
    sc = spy_close.get(d)
    se = spy_ema.get(d)
    above = (sc > se) if (sc is not None and se is not None) else None
    return {
        "date": d,
        "vix_close": vc,
        "vix_9d": v9,
        "vix_term_structure": term,
        "spy_close": sc,
        "spy_ema_200": se,
        "spy_above_200ema": above,
    }


def _by_date(records: Iterable[IndexBarRecord]) -> dict[date, float]:
    return {r.date: r.close for r in records}


def _spy_close_by_date(session: Session, symbol: str, lookback_days: int) -> dict[date, float]:
    rows = session.execute(
        select(BarDaily.date, BarDaily.close)
        .where(BarDaily.symbol == symbol)
        .order_by(BarDaily.date.desc())
        .limit(lookback_days)
    ).all()
    return {d: float(c) for d, c in rows}


def _spy_ema_by_date(session: Session, symbol: str, lookback_days: int) -> dict[date, float]:
    rows = session.execute(
        select(IndicatorDaily.date, IndicatorDaily.ema_200)
        .where(IndicatorDaily.symbol == symbol)
        .where(IndicatorDaily.ema_200.isnot(None))
        .order_by(IndicatorDaily.date.desc())
        .limit(lookback_days)
    ).all()
    return {d: float(e) for d, e in rows}


def _upsert(session: Session, rows: list[dict[str, object]]) -> int:
    stmt = sqlite_insert(MacroDaily).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[MacroDaily.date],
        set_={
            "vix_close": stmt.excluded.vix_close,
            "vix_9d": stmt.excluded.vix_9d,
            "vix_term_structure": stmt.excluded.vix_term_structure,
            "spy_close": stmt.excluded.spy_close,
            "spy_ema_200": stmt.excluded.spy_ema_200,
            "spy_above_200ema": stmt.excluded.spy_above_200ema,
        },
    )
    session.execute(stmt)
    return len(rows)
