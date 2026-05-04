"""Seed default screener configs.

Run via ``python -m scripts.seed_filter_configs``. Idempotent — re-running
leaves existing configs (matched by name) untouched.

The first config mirrors the example in
``docs/planning/02-screener-filters.md`` §"Filter config example": a
conservative wheel setup that wants quality names pulling back to the 200
EMA in a high-IV environment with no earnings inside the option's
lifetime. The remaining configs cover a spread of wheel-friendly setups
(premium hunting, mean-reversion, blue-chip income, trend pullback, IV
spike) so the user has meaningful selection in the screener / backtest
UIs out of the box.
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

TRUE_WHEEL_CONFIG: dict[str, Any] = {
    "name": "True Wheel - 200EMA Touch",
    "description": (
        "Same entry signals as the conservative 200EMA touch, but intended "
        "for backtests run with --hold-losers-to-expiry: ITM puts ride to "
        "assignment (no buy-back at a loss) and the wheel pivots to covered "
        "calls floored at the share cost basis."
    ),
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

PREMIUM_HUNTER_CONFIG: dict[str, Any] = {
    "name": "Premium Hunter - High IV Rank",
    "description": (
        "Aggressive premium capture on uptrending names with elevated IV — "
        "looser EMA proximity, harder IV-rank floor."
    ),
    "filters": [
        {"id": "weekly_above_200ema", "required": True},
        {"id": "iv_rank_high", "params": {"min": 70}, "required": True},
        {"id": "iv_above_hv", "params": {"min_ratio": 1.05}},
        {"id": "near_50ema", "params": {"max_pct": 0.05}},
        {"id": "rsi_oversold", "params": {"max_rsi": 55}},
        {"id": "iv_percentile_high", "params": {"min": 60}},
        {"id": "no_earnings_in_window", "params": {"days": 35}, "required": True},
        {"id": "min_market_cap", "params": {"min_usd": 5_000_000_000}},
        {"id": "tier_allowed", "params": {"tiers": [1, 2]}},
        {"id": "not_freefall", "params": {"min_5d_return": -0.12}},
        {"id": "sector_concentration", "params": {"max": 4}},
    ],
    "scoring": {
        "weights": {
            "iv_rank_high": 0.40,
            "iv_percentile_high": 0.25,
            "near_50ema": 0.20,
            "rsi_oversold": 0.15,
        }
    },
}

BOLLINGER_REVERSAL_CONFIG: dict[str, Any] = {
    "name": "Bollinger Bottom Reversal",
    "description": (
        "Mean-reversion entry: lower-band touch on quality names with "
        "premium-rich IV and no near-term earnings."
    ),
    "filters": [
        {"id": "bb_lower_touch", "required": True},
        {"id": "rsi_oversold", "params": {"max_rsi": 35}, "required": True},
        {"id": "iv_percentile_high", "params": {"min": 40}},
        {"id": "iv_rank_high", "params": {"min": 40}},
        {"id": "iv_above_hv", "params": {"min_ratio": 1.0}},
        {"id": "not_freefall", "params": {"min_5d_return": -0.15}, "required": True},
        {"id": "no_earnings_in_window", "params": {"days": 30}, "required": True},
        {"id": "min_market_cap", "params": {"min_usd": 2_000_000_000}},
        {"id": "tier_allowed", "params": {"tiers": [1, 2, 3]}},
        {"id": "sector_concentration", "params": {"max": 3}},
    ],
    "scoring": {
        "weights": {
            "rsi_oversold": 0.40,
            "iv_percentile_high": 0.30,
            "iv_rank_high": 0.30,
        }
    },
}

BLUE_CHIP_INCOME_CONFIG: dict[str, Any] = {
    "name": "Blue Chip Income",
    "description": (
        "Tier-1 mega-caps only — modest premium expectations, tight "
        "concentration, long earnings buffer."
    ),
    "filters": [
        {"id": "weekly_above_200ema", "required": True},
        {"id": "tier_allowed", "params": {"tiers": [1]}, "required": True},
        {"id": "min_market_cap", "params": {"min_usd": 50_000_000_000}, "required": True},
        {"id": "no_earnings_in_window", "params": {"days": 45}, "required": True},
        {"id": "near_200ema", "params": {"max_pct": 0.05}},
        {"id": "rsi_oversold", "params": {"max_rsi": 50}},
        {"id": "iv_percentile_high", "params": {"min": 40}},
        {"id": "not_freefall", "params": {"min_5d_return": -0.08}},
        {"id": "sector_concentration", "params": {"max": 2}},
    ],
    "scoring": {
        "weights": {
            "iv_percentile_high": 0.40,
            "near_200ema": 0.30,
            "rsi_oversold": 0.30,
        }
    },
}

TREND_PULLBACK_CONFIG: dict[str, Any] = {
    "name": "Trend Pullback - 50EMA Bounce",
    "description": (
        "Bullish-continuation setup: established uptrend pulling back to "
        "the 50 EMA with moderate IV and no earnings."
    ),
    "filters": [
        {"id": "weekly_above_200ema", "required": True},
        {"id": "near_50ema", "params": {"max_pct": 0.025}, "required": True},
        {"id": "rsi_oversold", "params": {"max_rsi": 55}},
        {"id": "iv_percentile_high", "params": {"min": 35}},
        {"id": "iv_rank_high", "params": {"min": 35}},
        {"id": "no_earnings_in_window", "params": {"days": 35}, "required": True},
        {"id": "min_market_cap", "params": {"min_usd": 10_000_000_000}},
        {"id": "tier_allowed", "params": {"tiers": [1, 2]}},
        {"id": "not_freefall", "params": {"min_5d_return": -0.10}},
        {"id": "sector_concentration", "params": {"max": 3}},
    ],
    "scoring": {
        "weights": {
            "near_50ema": 0.40,
            "iv_percentile_high": 0.20,
            "iv_rank_high": 0.20,
            "rsi_oversold": 0.20,
        }
    },
}

IV_SPIKE_CONFIG: dict[str, Any] = {
    "name": "Volatility Spike Hunter",
    "description": (
        "Extreme IV regime: rank ≥ 75 and IV/HV stretch — best for "
        "short-dated CSPs that capitalize on vol mean-reversion."
    ),
    "filters": [
        {"id": "iv_rank_high", "params": {"min": 75}, "required": True},
        {"id": "iv_above_hv", "params": {"min_ratio": 1.15}, "required": True},
        {"id": "iv_percentile_high", "params": {"min": 65}},
        {"id": "rsi_oversold", "params": {"max_rsi": 50}},
        {"id": "no_earnings_in_window", "params": {"days": 21}, "required": True},
        {"id": "min_market_cap", "params": {"min_usd": 10_000_000_000}, "required": True},
        {"id": "tier_allowed", "params": {"tiers": [1, 2]}},
        {"id": "not_freefall", "params": {"min_5d_return": -0.15}},
        {"id": "sector_concentration", "params": {"max": 3}},
    ],
    "scoring": {
        "weights": {
            "iv_rank_high": 0.45,
            "iv_percentile_high": 0.30,
            "rsi_oversold": 0.25,
        }
    },
}

ALL_CONFIGS: tuple[dict[str, Any], ...] = (
    DEFAULT_CONFIG,
    TRUE_WHEEL_CONFIG,
    PREMIUM_HUNTER_CONFIG,
    BOLLINGER_REVERSAL_CONFIG,
    BLUE_CHIP_INCOME_CONFIG,
    TREND_PULLBACK_CONFIG,
    IV_SPIKE_CONFIG,
)


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
        for spec in ALL_CONFIGS:
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
