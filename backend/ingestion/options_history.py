"""Historical option chain backfill from Polygon.

Strategy: per symbol, enumerate every contract whose expiration falls in the
backfill window via ``list_contracts`` (with ``expired=true`` so matured
contracts are included), then pull each contract's daily OHLCV bars in one
``get_contract_aggs`` call. Bars are filtered to ``[start, end]`` and
upserted into ``options_historical`` keyed on
``(symbol, as_of, expiration, strike, option_type)``.

The strike-window filter is anchored on each day's underlying close so the
backfilled chain mirrors what live ingestion would have stored — anything
outside ±``strike_pct_window`` of spot for a given ``as_of`` is skipped.
This keeps the table from ballooning with deep-OTM strikes the simulator
will never reach.

Idempotent: re-runs over the same window upsert in place.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

import click
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.config import get_settings
from core.logging import configure_logging, get_logger
from db import get_session
from db.models.market import BarDaily, OptionsHistorical, Ticker
from ingestion.polygon_client import (
    OptionContractRef,
    OptionDailyAgg,
    PolygonError,
    PolygonOptionsClient,
)

log = get_logger(__name__)

DEFAULT_MAX_DTE = 60
DEFAULT_STRIKE_PCT_WINDOW = 0.15


class HistoricalSource(Protocol):
    """Polygon-shaped client. Defined here so tests can swap in a fake."""

    def list_contracts(
        self,
        underlying: str,
        *,
        as_of: date | None = ...,
        expiration_gte: date | None = ...,
        expiration_lte: date | None = ...,
        strike_gte: float | None = ...,
        strike_lte: float | None = ...,
        include_expired: bool = ...,
    ) -> list[OptionContractRef]: ...

    def get_contract_aggs(
        self,
        occ: str,
        *,
        from_date: date,
        to_date: date,
        adjusted: bool = ...,
    ) -> list[OptionDailyAgg]: ...


@dataclass
class BackfillSummary:
    symbols_requested: int
    symbols_with_data: int
    contracts_fetched: int
    rows_written: int


def backfill_history(
    session: Session,
    client: HistoricalSource,
    symbols: list[str] | None = None,
    *,
    start: date,
    end: date,
    max_dte: int = DEFAULT_MAX_DTE,
    strike_pct_window: float = DEFAULT_STRIKE_PCT_WINDOW,
) -> BackfillSummary:
    """Backfill ``options_historical`` for ``symbols`` over ``[start, end]``.

    For each symbol:
    1. Build a per-day spot map from ``bars_daily`` (skip days with no bar).
    2. Enumerate all contracts whose expiration falls in
       ``[start, end + max_dte]`` via Polygon's reference endpoint.
    3. Fetch each contract's daily aggs over ``[start, expiration]``.
    4. For each bar, compute the day's spot-relative strike window and only
       insert rows whose strike sits inside it.
    """
    target = _resolve_symbols(session, symbols)
    fetched_at = datetime.now(UTC)

    contracts_fetched = 0
    rows_written = 0
    symbols_with_data = 0

    for symbol in target:
        spot_map = _spot_map(session, symbol, start=start, end=end)
        if not spot_map:
            log.warning("options_history.no_bars_skipped", symbol=symbol)
            continue

        contracts = client.list_contracts(
            symbol,
            expiration_gte=start,
            expiration_lte=end + timedelta(days=max_dte),
            include_expired=True,
        )
        if not contracts:
            log.info("options_history.no_contracts", symbol=symbol)
            continue

        symbol_rows = 0
        for contract in contracts:
            window_to = min(contract.expiration, end)
            if window_to < start:
                continue
            try:
                bars = client.get_contract_aggs(contract.occ, from_date=start, to_date=window_to)
            except PolygonError as exc:
                log.warning(
                    "options_history.contract_fetch_failed",
                    symbol=symbol,
                    occ=contract.occ,
                    reason=str(exc),
                )
                continue
            contracts_fetched += 1

            rows = list(_rows_for_contract(contract, bars, spot_map, strike_pct_window))
            written = _upsert_rows(session, symbol, rows, fetched_at=fetched_at)
            symbol_rows += written

        if symbol_rows > 0:
            symbols_with_data += 1
            rows_written += symbol_rows
            session.commit()
        log.info(
            "options_history.symbol_done",
            symbol=symbol,
            contracts=len(contracts),
            rows=symbol_rows,
        )

    log.info(
        "options_history.fetch.done",
        symbols=len(target),
        with_data=symbols_with_data,
        contracts=contracts_fetched,
        rows=rows_written,
    )
    return BackfillSummary(
        symbols_requested=len(target),
        symbols_with_data=symbols_with_data,
        contracts_fetched=contracts_fetched,
        rows_written=rows_written,
    )


def _rows_for_contract(
    contract: OptionContractRef,
    bars: Iterable[OptionDailyAgg],
    spot_map: dict[date, float],
    strike_pct_window: float,
) -> Iterable[dict[str, object]]:
    for bar in bars:
        spot = spot_map.get(bar.date)
        if spot is None:
            # No underlying bar for this day — drop it. Indicators won't
            # have anything to anchor IV against either.
            continue
        lo = spot * (1.0 - strike_pct_window)
        hi = spot * (1.0 + strike_pct_window)
        if not (lo <= contract.strike <= hi):
            continue
        yield {
            "symbol": contract.underlying,
            "as_of": bar.date,
            "expiration": contract.expiration,
            "strike": contract.strike,
            "option_type": contract.option_type,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "open_interest": None,
        }


def _upsert_rows(
    session: Session,
    symbol: str,
    rows: list[dict[str, object]],
    *,
    fetched_at: datetime,
) -> int:
    if not rows:
        return 0
    payload = [{**r, "fetched_at": fetched_at} for r in rows]
    stmt = sqlite_insert(OptionsHistorical).values(payload)
    update_cols = {
        col: getattr(stmt.excluded, col)
        for col in ("open", "high", "low", "close", "volume", "open_interest", "fetched_at")
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            OptionsHistorical.symbol,
            OptionsHistorical.as_of,
            OptionsHistorical.expiration,
            OptionsHistorical.strike,
            OptionsHistorical.option_type,
        ],
        set_=update_cols,
    )
    session.execute(stmt)
    _ = symbol
    return len(payload)


def _resolve_symbols(session: Session, symbols: list[str] | None) -> list[str]:
    if symbols:
        return symbols
    rows = session.execute(
        select(Ticker.symbol).where(Ticker.is_active.is_(True)).order_by(Ticker.symbol)
    ).all()
    return [r[0] for r in rows]


def _spot_map(session: Session, symbol: str, *, start: date, end: date) -> dict[date, float]:
    rows = session.execute(
        select(BarDaily.date, BarDaily.close)
        .where(BarDaily.symbol == symbol)
        .where(BarDaily.date >= start)
        .where(BarDaily.date <= end)
    ).all()
    return {r[0]: float(r[1]) for r in rows}


@click.command(context_settings={"show_default": True})
@click.option("--start", required=True, help="Backfill start date (YYYY-MM-DD).")
@click.option("--end", required=True, help="Backfill end date (YYYY-MM-DD, inclusive).")
@click.option(
    "--symbols",
    default=None,
    help="Comma-separated symbols; default = every active ticker.",
)
@click.option(
    "--max-dte",
    type=int,
    default=DEFAULT_MAX_DTE,
    help="Don't enumerate contracts whose expiration is more than this many days past --end.",
)
@click.option(
    "--strike-pct-window",
    type=float,
    default=DEFAULT_STRIKE_PCT_WINDOW,
    help="Per-day strike filter as a fraction of underlying close (mirrors live ingestion).",
)
def cli(
    start: str,
    end: str,
    symbols: str | None,
    max_dte: int,
    strike_pct_window: float,
) -> None:
    """Backfill historical option chains from Polygon into options_historical."""
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)

    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    if end_date < start_date:
        raise click.BadParameter("--end must be on or after --start")

    symbol_list = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    client = PolygonOptionsClient()

    with get_session() as session:
        summary = backfill_history(
            session,
            client,
            symbol_list,
            start=start_date,
            end=end_date,
            max_dte=max_dte,
            strike_pct_window=strike_pct_window,
        )

    click.echo(
        f"symbols={summary.symbols_requested} "
        f"with_data={summary.symbols_with_data} "
        f"contracts={summary.contracts_fetched} "
        f"rows_written={summary.rows_written}"
    )


if __name__ == "__main__":
    cli()
