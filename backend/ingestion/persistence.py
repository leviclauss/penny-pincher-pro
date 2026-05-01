"""Persistence helpers shared across ingestion modules."""

from __future__ import annotations

from datetime import date
from typing import cast

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from db.models.market import BarDaily, IndicatorDaily, OptionsSnapshot
from ingestion.indicators import INDICATOR_COLUMNS
from ingestion.options_client import OptionSnapshotRecord

INDICATOR_NUMERIC_COLUMNS: tuple[str, ...] = INDICATOR_COLUMNS
IV_ONLY_COLUMNS: tuple[str, ...] = ("iv_atm", "iv_rank", "iv_percentile")


def load_bars(session: Session, symbol: str) -> pd.DataFrame:
    """Read all stored daily bars for a symbol as a DataFrame indexed by date."""
    rows = session.execute(
        select(
            BarDaily.date,
            BarDaily.open,
            BarDaily.high,
            BarDaily.low,
            BarDaily.close,
            BarDaily.volume,
        )
        .where(BarDaily.symbol == symbol)
        .order_by(BarDaily.date)
    ).all()
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df.index = pd.DatetimeIndex(df["date"])
    df.index.name = "date"
    return df.drop(columns=["date"])


def upsert_indicators(
    session: Session,
    symbol: str,
    indicators: pd.DataFrame,
    *,
    only_dates: list[date] | None = None,
) -> int:
    """Upsert indicator rows for ``symbol``.

    If ``only_dates`` is provided, only rows for those dates are written; this
    is the path used by the incremental pipeline so we don't rewrite history
    every run. NaN values are stored as NULL.
    """
    if indicators.empty:
        return 0

    if only_dates is None:
        target = indicators
    else:
        target_index = pd.to_datetime(only_dates)
        target = indicators.loc[indicators.index.isin(target_index)]
    if target.empty:
        return 0

    rows: list[dict[str, object]] = []
    for idx, row in target.iterrows():
        row_date = idx if isinstance(idx, date) else cast(pd.Timestamp, idx).date()
        record: dict[str, object] = {"symbol": symbol, "date": row_date}
        for col in INDICATOR_NUMERIC_COLUMNS:
            value = row.get(col)
            record[col] = None if pd.isna(value) else float(value)
        rows.append(record)

    stmt = sqlite_insert(IndicatorDaily).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[IndicatorDaily.symbol, IndicatorDaily.date],
        set_={col: getattr(stmt.excluded, col) for col in INDICATOR_NUMERIC_COLUMNS},
    )
    session.execute(stmt)
    return len(rows)


def upsert_iv_indicators(
    session: Session,
    symbol: str,
    as_of: date,
    *,
    iv_atm: float | None,
    iv_rank: float | None,
    iv_percentile: float | None,
) -> None:
    """Upsert just the three IV columns for ``(symbol, as_of)``.

    On INSERT the technical-indicator columns remain NULL (the bars/indicator
    pass populates them separately). On UPDATE the existing technical
    indicators are preserved — only IV columns are touched.
    """
    row = {
        "symbol": symbol,
        "date": as_of,
        "iv_atm": iv_atm,
        "iv_rank": iv_rank,
        "iv_percentile": iv_percentile,
    }
    stmt = sqlite_insert(IndicatorDaily).values([row])
    stmt = stmt.on_conflict_do_update(
        index_elements=[IndicatorDaily.symbol, IndicatorDaily.date],
        set_={col: getattr(stmt.excluded, col) for col in IV_ONLY_COLUMNS},
    )
    session.execute(stmt)


def load_options_chain(session: Session, symbol: str) -> list[OptionSnapshotRecord]:
    """Read the current ``options_snapshot`` rows for ``symbol`` as records."""
    rows = (
        session.execute(select(OptionsSnapshot).where(OptionsSnapshot.symbol == symbol))
        .scalars()
        .all()
    )
    return [
        OptionSnapshotRecord(
            symbol=r.symbol,
            expiration=r.expiration,
            strike=r.strike,
            option_type=r.option_type,
            bid=r.bid,
            ask=r.ask,
            last=r.last,
            volume=r.volume,
            open_interest=r.open_interest,
            delta=r.delta,
            gamma=r.gamma,
            theta=r.theta,
            vega=r.vega,
            iv=r.iv,
        )
        for r in rows
    ]


def load_iv_history(
    session: Session, symbol: str, *, before: date, days: int = 252
) -> list[float | None]:
    """Past ``days`` of ``iv_atm`` values for ``symbol``, oldest first."""
    rows = session.execute(
        select(IndicatorDaily.iv_atm)
        .where(IndicatorDaily.symbol == symbol)
        .where(IndicatorDaily.date < before)
        .order_by(IndicatorDaily.date.desc())
        .limit(days)
    ).all()
    return [r[0] for r in reversed(rows)]
