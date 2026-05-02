"""Option chain fetcher: pulls current chains and upserts to options_snapshot.

The schema treats ``options_snapshot`` as a current-only table (overwritten
daily) — historical chains aren't preserved. Each ingestion run replaces the
prior snapshot for a given (symbol, expiration, strike, option_type) row.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.logging import get_logger
from core.time import market_today
from db.models.market import BarDaily, OptionsSnapshot, Ticker
from ingestion.options_client import OptionSnapshotRecord


class ChainSource(Protocol):
    """Anything that returns ``OptionSnapshotRecord``s for a symbol."""

    def get_chain(
        self,
        underlying: str,
        *,
        expiration_gte: date | None = ...,
        expiration_lte: date | None = ...,
        strike_gte: float | None = ...,
        strike_lte: float | None = ...,
    ) -> list[OptionSnapshotRecord]: ...


log = get_logger(__name__)

DEFAULT_MAX_DTE = 60
DEFAULT_STRIKE_PCT_WINDOW = 0.15


@dataclass
class OptionsFetchSummary:
    symbols_requested: int
    symbols_with_data: int
    chains_written: int
    contracts_written: int


def fetch_chains(
    session: Session,
    client: ChainSource,
    symbols: list[str] | None = None,
    *,
    max_dte: int = DEFAULT_MAX_DTE,
    strike_pct_window: float = DEFAULT_STRIKE_PCT_WINDOW,
    as_of: date | None = None,
    replace_existing: bool = True,
) -> OptionsFetchSummary:
    """Fetch option chains for ``symbols`` (or every active ticker).

    Per symbol: looks up latest stored close to bound the strike window, then
    asks Alpaca for chains within ``[as_of, as_of + max_dte]`` and
    ``±strike_pct_window`` of spot. Result is upserted into
    ``options_snapshot``. With ``replace_existing=True`` the symbol's prior
    snapshot rows are deleted first so stale strikes don't linger.
    """
    target = _resolve_symbols(session, symbols)
    today = as_of or market_today()
    expiration_lte = today + timedelta(days=max_dte)
    snapshot_at = datetime.now(UTC)

    chains_written = 0
    contracts_written = 0
    symbols_with_data = 0

    for symbol in target:
        spot = _latest_close(session, symbol)
        if spot is None:
            log.warning("options.no_spot_skipped", symbol=symbol)
            continue

        strike_gte = round(spot * (1.0 - strike_pct_window), 2)
        strike_lte = round(spot * (1.0 + strike_pct_window), 2)

        records = client.get_chain(
            symbol,
            expiration_gte=today,
            expiration_lte=expiration_lte,
            strike_gte=strike_gte,
            strike_lte=strike_lte,
        )
        if not records:
            log.info("options.empty_chain", symbol=symbol)
            continue

        if replace_existing:
            session.execute(delete(OptionsSnapshot).where(OptionsSnapshot.symbol == symbol))

        written = _upsert_snapshots(session, records, snapshot_at)
        contracts_written += written
        chains_written += 1
        symbols_with_data += 1
        session.commit()

    log.info(
        "options.fetch.done",
        symbols=len(target),
        with_data=symbols_with_data,
        contracts=contracts_written,
    )
    return OptionsFetchSummary(
        symbols_requested=len(target),
        symbols_with_data=symbols_with_data,
        chains_written=chains_written,
        contracts_written=contracts_written,
    )


def _upsert_snapshots(
    session: Session, records: Iterable[OptionSnapshotRecord], snapshot_at: datetime
) -> int:
    rows = [
        {
            "symbol": r.symbol,
            "expiration": r.expiration,
            "strike": r.strike,
            "option_type": r.option_type,
            "bid": r.bid,
            "ask": r.ask,
            "last": r.last,
            "volume": r.volume,
            "open_interest": r.open_interest,
            "delta": r.delta,
            "gamma": r.gamma,
            "theta": r.theta,
            "vega": r.vega,
            "iv": r.iv,
            "snapshot_at": snapshot_at,
        }
        for r in records
    ]
    if not rows:
        return 0

    stmt = sqlite_insert(OptionsSnapshot).values(rows)
    update_cols = {
        col: getattr(stmt.excluded, col)
        for col in (
            "bid",
            "ask",
            "last",
            "volume",
            "open_interest",
            "delta",
            "gamma",
            "theta",
            "vega",
            "iv",
            "snapshot_at",
        )
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            OptionsSnapshot.symbol,
            OptionsSnapshot.expiration,
            OptionsSnapshot.strike,
            OptionsSnapshot.option_type,
        ],
        set_=update_cols,
    )
    session.execute(stmt)
    return len(rows)


def _resolve_symbols(session: Session, symbols: list[str] | None) -> list[str]:
    if symbols:
        return symbols
    rows = session.execute(
        select(Ticker.symbol).where(Ticker.is_active.is_(True)).order_by(Ticker.symbol)
    ).all()
    return [r[0] for r in rows]


def _latest_close(session: Session, symbol: str) -> float | None:
    row = session.execute(
        select(BarDaily.close)
        .where(BarDaily.symbol == symbol)
        .order_by(BarDaily.date.desc())
        .limit(1)
    ).first()
    return float(row[0]) if row else None
