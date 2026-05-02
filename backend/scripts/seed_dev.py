"""Seed a small watchlist for local development.

Run via ``python -m scripts.seed_dev``. Idempotent — re-running is a no-op for
already-present symbols. Real watchlist curation belongs in the UI later.
"""

from __future__ import annotations

import click
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.config import get_settings
from core.logging import configure_logging, get_logger
from core.time import utcnow
from db import get_session
from db.models.market import Ticker

DEV_WATCHLIST: tuple[tuple[str, str, int], ...] = (
    ("AAPL", "Apple Inc.", 1),
    ("MSFT", "Microsoft Corp.", 1),
    ("NVDA", "NVIDIA Corp.", 2),
    ("AMD", "Advanced Micro Devices", 2),
    ("GOOGL", "Alphabet Inc.", 1),
    ("META", "Meta Platforms", 1),
    ("AMZN", "Amazon.com", 1),
    ("SPY", "SPDR S&P 500 ETF", 1),
    ("QQQ", "Invesco QQQ Trust", 1),
    ("TSLA", "Tesla Inc.", 2),
)

log = get_logger(__name__)


@click.command()
def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level)
    now = utcnow()

    rows = [
        {
            "symbol": sym,
            "name": name,
            "tier": tier,
            "is_active": True,
            "is_hidden": False,
            "added_at": now,
            "updated_at": now,
        }
        for sym, name, tier in DEV_WATCHLIST
    ]

    with get_session() as session:
        stmt = sqlite_insert(Ticker).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=[Ticker.symbol])
        session.execute(stmt)
        log.info("seed.dev.done", total=len(rows))

    click.echo(f"Seeded {len(rows)} watchlist tickers (existing rows untouched).")


if __name__ == "__main__":
    main()
