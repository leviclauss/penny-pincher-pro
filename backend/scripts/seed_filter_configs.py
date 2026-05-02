"""Seed default screener configs.

Run via ``python -m scripts.seed_filter_configs``. Idempotent — re-running
leaves existing configs (matched by name) untouched.

The default config mirrors the example in
``docs/planning/02-screener-filters.md`` §"Filter config example": a
conservative wheel setup that wants quality names pulling back to the 200
EMA in a high-IV environment with no earnings inside the option's
lifetime.
"""

from __future__ import annotations

from typing import Any

import click

from core.config import get_settings
from core.logging import configure_logging, get_logger
from core.time import utcnow
from db import get_session
from db.models.screener import FilterConfig

log = get_logger(__name__)

DEFAULT_CONFIG_NAME = "Conservative Wheel - 200EMA Touch"
DEFAULT_CONFIG_DESCRIPTION = "High-IV pullbacks to long-term support on quality names"

DEFAULT_CONFIG: dict[str, Any] = {
    "name": DEFAULT_CONFIG_NAME,
    "description": DEFAULT_CONFIG_DESCRIPTION,
    "filters": [
        {"id": "weekly_above_200ema", "required": True},
        {"id": "near_200ema", "params": {"max_pct": 0.03}},
        {"id": "rsi_oversold", "params": {"max_rsi": 40}},
        {"id": "iv_percentile_high", "params": {"min": 50}},
        {"id": "no_earnings_in_window", "params": {"days": 45}, "required": True},
        {"id": "min_market_cap", "params": {"min_usd": 10_000_000_000}},
        {"id": "tier_allowed", "params": {"tiers": [1, 2]}},
        {"id": "not_freefall", "params": {"min_5d_return": -0.10}},
        {"id": "sector_concentration", "params": {"max": 3}},
    ],
    "scoring": {
        "weights": {
            "iv_percentile_high": 0.35,
            "near_200ema": 0.25,
            "rsi_oversold": 0.25,
            "iv_rank_high": 0.15,
        }
    },
}


@click.command()
def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level)
    inserted, skipped = upsert_defaults()
    click.echo(f"Filter configs: {inserted} inserted, {skipped} already present.")


def upsert_defaults() -> tuple[int, int]:
    """Insert default configs that don't already exist (matched by name)."""
    inserted = 0
    skipped = 0
    now = utcnow()
    with get_session() as session:
        for spec in (DEFAULT_CONFIG,):
            existing = (
                session.query(FilterConfig).filter(FilterConfig.name == spec["name"]).one_or_none()
            )
            if existing is not None:
                skipped += 1
                continue
            session.add(
                FilterConfig(
                    name=spec["name"],
                    description=spec.get("description"),
                    config_json=spec,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            inserted += 1
            log.info("seed.filter_config.inserted", name=spec["name"])
    return inserted, skipped


if __name__ == "__main__":
    main()
