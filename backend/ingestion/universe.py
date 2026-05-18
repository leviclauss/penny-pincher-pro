"""Blue-chip + mid/small-cap universe management.

Loads the bundled symbol lists (S&P 100 large-cap, S&P 400 mid-cap,
S&P 600 small-cap) and upserts symbols into the ``tickers`` table with
``ticker_source='universe'``.

Watchlist-sourced tickers (``ticker_source='watchlist'``) are never
downgraded — watchlist always takes precedence. New universe tickers
land with ``is_active=True`` so the nightly evening pipeline ingests their
bars/indicators/options automatically, and ``is_hidden=True`` so they
don't appear in the main watchlist UI.

Tier mapping (drives the ``tier_allowed`` screener filter):

- tier 1 / 2 — S&P 100 (existing curated split inherited from
  ``universe_list.json``).
- tier 3 — S&P 400 mid-caps (``universe_sp400.json``).
- tier 4 — S&P 600 small-caps (``universe_sp600.json``).

Sectors are not bundled — ``ingestion.ticker_metadata`` fetches them from
Finnhub. Re-run that one-shot after a fresh sync so the new rows pick up
sector + market_cap, both of which power the ``sector_allowed`` and
``min_market_cap`` filters.

The bundled lists are a point-in-time snapshot of index membership and
will drift on index reconstitutions. Treat them as a starting universe,
not a live feed — symbols that have since been delisted or renamed are
caught lazily by the per-symbol ingestion pipeline (which logs and
skips) rather than blocking the sync.

Usage::

    python -m ingestion.universe          # sync all bundled lists once
    python -m ingestion.universe --list   # print the bundled symbol list
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import click
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.logging import get_logger
from core.time import utcnow
from db import get_session
from db.models.market import Ticker

log = get_logger(__name__)

_INGESTION_DIR = Path(__file__).parent
UNIVERSE_LIST_PATH = _INGESTION_DIR / "universe_list.json"
UNIVERSE_SP400_PATH = _INGESTION_DIR / "universe_sp400.json"
UNIVERSE_SP600_PATH = _INGESTION_DIR / "universe_sp600.json"

DEFAULT_UNIVERSE_PATHS: tuple[Path, ...] = (
    UNIVERSE_LIST_PATH,
    UNIVERSE_SP400_PATH,
    UNIVERSE_SP600_PATH,
)


@dataclass(slots=True)
class UniverseSyncSummary:
    total: int
    inserted: int
    skipped_watchlist: int
    skipped_already_universe: int


def load_universe_list(path: Path | None = None) -> list[dict[str, str | int | None]]:
    """Load a single bundled JSON list, optionally overriding the path for tests."""
    p = path or UNIVERSE_LIST_PATH
    with open(p) as fh:
        data: object = json.load(fh)
    if not isinstance(data, list):
        raise ValueError(f"universe list must be a JSON array, got {type(data)}")
    return data


def load_all_universe_lists(
    paths: tuple[Path, ...] | None = None,
) -> list[dict[str, str | int | None]]:
    """Load every bundled list and concatenate.

    Later paths cannot overwrite earlier entries for the same symbol —
    the first occurrence wins. With the default ordering this means S&P
    100 tier assignments take precedence over the mid/small-cap files
    if a symbol appears in both (e.g. promotions/relegations between
    snapshots).
    """
    seen: dict[str, dict[str, str | int | None]] = {}
    for path in paths or DEFAULT_UNIVERSE_PATHS:
        if not path.exists():
            log.warning("universe.list_missing", path=str(path))
            continue
        for entry in load_universe_list(path):
            sym = str(entry["symbol"])
            if sym in seen:
                continue
            seen[sym] = entry
    return list(seen.values())


def sync_universe_tickers(
    session: Session,
    *,
    universe_paths: tuple[Path, ...] | None = None,
) -> UniverseSyncSummary:
    """Upsert every bundled symbol into ``tickers`` as ``ticker_source='universe'``.

    Rules:
    - Already ``watchlist``: skip (watchlist takes precedence).
    - Already ``universe``: skip (already managed).
    - Not present: insert with ``is_active=True``, ``is_hidden=True``,
      ``ticker_source='universe'``.
    """
    entries = load_all_universe_lists(universe_paths)
    symbols = [str(e["symbol"]) for e in entries]

    existing: dict[str, Ticker] = {
        t.symbol: t
        for t in session.execute(select(Ticker).where(Ticker.symbol.in_(symbols))).scalars()
    }

    inserted = 0
    skipped_watchlist = 0
    skipped_already_universe = 0

    for entry in entries:
        sym = str(entry["symbol"])
        row = existing.get(sym)

        if row is not None:
            if row.ticker_source == "watchlist":
                skipped_watchlist += 1
                continue
            # Already universe — nothing to update (metadata may drift; that's fine).
            skipped_already_universe += 1
            continue

        now = utcnow()
        ticker = Ticker(
            symbol=sym,
            name=str(entry.get("name") or ""),
            tier=int(entry["tier"]) if "tier" in entry and entry["tier"] is not None else None,
            is_active=True,
            is_hidden=True,
            ticker_source="universe",
            added_at=now,
            updated_at=now,
        )
        session.add(ticker)
        inserted += 1

    session.commit()

    summary = UniverseSyncSummary(
        total=len(entries),
        inserted=inserted,
        skipped_watchlist=skipped_watchlist,
        skipped_already_universe=skipped_already_universe,
    )
    log.info(
        "universe.sync.done",
        total=summary.total,
        inserted=summary.inserted,
        skipped_watchlist=summary.skipped_watchlist,
        skipped_already_universe=summary.skipped_already_universe,
    )
    return summary


def get_universe_symbols(session: Session) -> list[str]:
    """Return all active universe-sourced symbol strings."""
    rows = session.execute(
        select(Ticker.symbol)
        .where(Ticker.ticker_source == "universe", Ticker.is_active.is_(True))
        .order_by(Ticker.symbol)
    ).scalars()
    return list(rows)


@click.command()
@click.option("--list", "list_only", is_flag=True, help="Print bundled symbols and exit.")
def main(list_only: bool) -> None:
    if list_only:
        entries = load_all_universe_lists()
        for e in entries:
            click.echo(f"{e['symbol']:<8} tier={e.get('tier')}  {e.get('name', '')}")
        click.echo(f"total: {len(entries)}")
        return

    with get_session() as session:
        summary = sync_universe_tickers(session)
    click.echo(
        f"Universe sync complete: {summary.inserted} inserted, "
        f"{summary.skipped_watchlist} skipped (watchlist), "
        f"{summary.skipped_already_universe} skipped (already universe). "
        f"Total universe symbols: {summary.total}."
    )


if __name__ == "__main__":
    main()
