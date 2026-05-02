"""Ticker metadata fetcher (sector, market_cap, name).

Pulls Finnhub's ``/stock/profile2`` per active ticker and updates the
``tickers`` row. Metadata changes slowly, so this is a separate one-shot
flow rather than part of the daily ingestion pipeline — re-run on demand
when new tickers are added or quarterly to refresh market caps.

Free tier: 60 calls/min. Each symbol is one call, so a 100-ticker
watchlist comfortably fits in a single minute. ETFs (SPY, QQQ) and
non-US symbols return an empty payload — those rows are left untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import click
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import get_settings
from core.logging import configure_logging, get_logger
from core.time import utcnow
from db import get_session
from db.models.market import Ticker
from ingestion.finnhub_client import CompanyProfile, FinnhubClient, FinnhubError

log = get_logger(__name__)


class CompanyProfileSource(Protocol):
    def get_company_profile(self, symbol: str) -> CompanyProfile | None: ...


@dataclass
class MetadataFetchSummary:
    symbols_processed: int
    rows_updated: int
    rows_skipped: int


def fetch_metadata(
    session: Session,
    client: CompanyProfileSource,
    symbols: list[str] | None = None,
    *,
    only_missing: bool = False,
) -> MetadataFetchSummary:
    """Fetch profile data for active tickers and update sector/market_cap/name.

    ``only_missing=True`` skips tickers that already have a sector — useful
    after seeding new symbols without re-fetching everyone.
    """
    targets = _resolve_symbols(session, symbols, only_missing=only_missing)
    updated = 0
    skipped = 0
    now = utcnow()

    for symbol in targets:
        profile = client.get_company_profile(symbol)
        if profile is None:
            log.info("metadata.no_profile_skipped", symbol=symbol)
            skipped += 1
            continue
        ticker = session.get(Ticker, symbol)
        if ticker is None:
            skipped += 1
            continue
        if profile.sector is not None:
            ticker.sector = profile.sector
        if profile.market_cap is not None:
            ticker.market_cap = profile.market_cap
        if profile.name and not ticker.name:
            ticker.name = profile.name
        ticker.updated_at = now
        updated += 1
        session.commit()

    log.info(
        "metadata.fetch.done",
        processed=len(targets),
        updated=updated,
        skipped=skipped,
    )
    return MetadataFetchSummary(
        symbols_processed=len(targets),
        rows_updated=updated,
        rows_skipped=skipped,
    )


def _resolve_symbols(
    session: Session, symbols: list[str] | None, *, only_missing: bool
) -> list[str]:
    stmt = select(Ticker.symbol).where(Ticker.is_active.is_(True))
    if only_missing:
        stmt = stmt.where(Ticker.sector.is_(None))
    if symbols:
        stmt = stmt.where(Ticker.symbol.in_(symbols))
    rows = session.execute(stmt.order_by(Ticker.symbol)).all()
    return [r[0] for r in rows]


def _build_client() -> CompanyProfileSource | None:
    try:
        return FinnhubClient()
    except FinnhubError:
        log.info("metadata.finnhub_unavailable", reason="missing_api_key")
        return None


@click.command(context_settings={"show_default": True})
@click.option(
    "--symbols",
    default=None,
    help="Comma-separated symbols; default = every active ticker.",
)
@click.option(
    "--only-missing",
    is_flag=True,
    default=False,
    help="Only refresh tickers whose sector is currently NULL.",
)
def cli(symbols: str | None, only_missing: bool) -> None:
    """Refresh ticker metadata (sector, market_cap, name) from Finnhub."""
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)

    client = _build_client()
    if client is None:
        raise click.ClickException("FINNHUB_API_KEY not set — cannot fetch metadata.")

    symbol_list: list[str] | None = (
        [s.strip().upper() for s in symbols.split(",")] if symbols else None
    )

    with get_session() as session:
        summary = fetch_metadata(session, client, symbol_list, only_missing=only_missing)

    click.echo(
        f"processed={summary.symbols_processed} "
        f"updated={summary.rows_updated} skipped={summary.rows_skipped}"
    )


if __name__ == "__main__":
    cli()
