"""Persistence helpers shared across ingestion modules."""

from __future__ import annotations

from datetime import date
from typing import cast

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from db.models.market import BarDaily, IndicatorDaily
from ingestion.indicators import INDICATOR_COLUMNS

INDICATOR_NUMERIC_COLUMNS: tuple[str, ...] = INDICATOR_COLUMNS


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
