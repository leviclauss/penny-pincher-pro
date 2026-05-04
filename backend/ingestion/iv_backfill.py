"""IV backfill from ``options_historical`` into ``indicators_daily``.

Replays ``compute_atm_iv`` over each ``(symbol, as_of)`` for which there's
both an underlying close and at least one stored options chain row. Once
this run finishes, ``iv_rank`` and ``iv_percentile`` will populate
immediately on the next pipeline pass — the 126-day warm-up no longer
applies because the historical chain seeds the rolling window.

This is a one-shot backfill; the daily evening pipeline still computes
``iv_atm`` from the live ``options_snapshot`` going forward.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import click
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from core.config import get_settings
from core.logging import configure_logging, get_logger
from db import get_session
from db.models.market import BarDaily, OptionsHistorical, Ticker
from ingestion.iv import compute_atm_iv
from ingestion.options_client import OptionSnapshotRecord
from ingestion.persistence import upsert_iv_indicators

log = get_logger(__name__)


@dataclass
class IVBackfillSummary:
    symbols_processed: int
    days_with_iv: int


def backfill_iv(
    session: Session,
    symbols: list[str] | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
) -> IVBackfillSummary:
    """Recompute ``iv_atm`` for each ``(symbol, as_of)`` with a stored chain.

    The backfill is bounded to ``[start, end]`` if either is provided; with
    both unset, every (symbol, as_of) in ``options_historical`` is replayed.
    Only ``iv_atm`` is written here — ``iv_rank``/``iv_percentile`` write
    NULL on this pass and are recomputed by the next normal pipeline run.
    """
    target = _resolve_symbols(session, symbols)
    settings = get_settings()
    days_with_iv = 0
    symbols_processed = 0

    for symbol in target:
        spot_map = _spot_map(session, symbol, start=start, end=end)
        if not spot_map:
            continue
        as_of_dates = _as_of_dates(session, symbol, start=start, end=end)
        if not as_of_dates:
            continue

        wrote_any = False
        for as_of in as_of_dates:
            spot = spot_map.get(as_of)
            if spot is None:
                continue
            chain = _load_historical_chain(session, symbol, as_of)
            if not chain:
                continue
            iv = compute_atm_iv(
                chain, spot=spot, as_of=as_of, risk_free_rate=settings.risk_free_rate
            )
            if iv is None:
                continue
            upsert_iv_indicators(
                session, symbol, as_of, iv_atm=iv, iv_rank=None, iv_percentile=None
            )
            days_with_iv += 1
            wrote_any = True
        if wrote_any:
            symbols_processed += 1
            session.commit()
        log.info(
            "iv_backfill.symbol_done",
            symbol=symbol,
            as_of_days=len(as_of_dates),
        )

    log.info("iv_backfill.done", symbols=symbols_processed, days_with_iv=days_with_iv)
    return IVBackfillSummary(symbols_processed=symbols_processed, days_with_iv=days_with_iv)


def _resolve_symbols(session: Session, symbols: list[str] | None) -> list[str]:
    if symbols:
        return symbols
    rows = session.execute(
        select(Ticker.symbol).where(Ticker.is_active.is_(True)).order_by(Ticker.symbol)
    ).all()
    return [r[0] for r in rows]


def _spot_map(
    session: Session, symbol: str, *, start: date | None, end: date | None
) -> dict[date, float]:
    stmt = select(BarDaily.date, BarDaily.close).where(BarDaily.symbol == symbol)
    if start is not None:
        stmt = stmt.where(BarDaily.date >= start)
    if end is not None:
        stmt = stmt.where(BarDaily.date <= end)
    rows = session.execute(stmt).all()
    return {r[0]: float(r[1]) for r in rows}


def _as_of_dates(
    session: Session, symbol: str, *, start: date | None, end: date | None
) -> list[date]:
    stmt = (
        select(distinct(OptionsHistorical.as_of))
        .where(OptionsHistorical.symbol == symbol)
        .order_by(OptionsHistorical.as_of)
    )
    if start is not None:
        stmt = stmt.where(OptionsHistorical.as_of >= start)
    if end is not None:
        stmt = stmt.where(OptionsHistorical.as_of <= end)
    return list(session.execute(stmt).scalars().all())


def _load_historical_chain(
    session: Session, symbol: str, as_of: date
) -> list[OptionSnapshotRecord]:
    rows = (
        session.execute(
            select(OptionsHistorical)
            .where(OptionsHistorical.symbol == symbol)
            .where(OptionsHistorical.as_of == as_of)
        )
        .scalars()
        .all()
    )
    out: list[OptionSnapshotRecord] = []
    for r in rows:
        # close is the historical mark; it stands in for both bid/ask (no
        # quote-level data on Polygon Developer) and last trade. compute_atm_iv
        # falls back to BS inversion of the mid when iv is None, so wiring
        # close into both bid/ask gives it a usable mid.
        out.append(
            OptionSnapshotRecord(
                symbol=r.symbol,
                expiration=r.expiration,
                strike=r.strike,
                option_type=r.option_type,
                bid=r.close,
                ask=r.close,
                last=r.close,
                volume=r.volume,
                open_interest=r.open_interest,
                delta=None,
                gamma=None,
                theta=None,
                vega=None,
                iv=None,
            )
        )
    return out


@click.command(context_settings={"show_default": True})
@click.option(
    "--symbols",
    default=None,
    help="Comma-separated symbols; default = every active ticker.",
)
@click.option("--start", default=None, help="Start date (YYYY-MM-DD); default = unbounded.")
@click.option("--end", default=None, help="End date (YYYY-MM-DD); default = unbounded.")
def cli(symbols: str | None, start: str | None, end: str | None) -> None:
    """Replay ATM IV from options_historical into indicators_daily.iv_atm."""
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)

    symbol_list = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    start_date = date.fromisoformat(start) if start else None
    end_date = date.fromisoformat(end) if end else None

    with get_session() as session:
        summary = backfill_iv(session, symbol_list, start=start_date, end=end_date)

    click.echo(f"symbols_processed={summary.symbols_processed} days_with_iv={summary.days_with_iv}")


if __name__ == "__main__":
    cli()
