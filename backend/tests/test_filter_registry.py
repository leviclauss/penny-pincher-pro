"""Registry parity: every catalog filter is registered, and vice versa.

The expected set is lifted verbatim from ``docs/planning/02-screener-filters.md``;
it's a mechanical guard against the registry and the doc drifting apart.
"""

from __future__ import annotations

import pytest

from screener.registry import FILTER_REGISTRY, UnknownFilterError, all_ids, resolve

# sector_concentration is intentionally absent — it's a Postprocessor
# (cross-symbol) that lands in PR3, not a Filter.
EXPECTED_FILTER_IDS = {
    # Tier 1
    "near_200ema",
    "near_50ema",
    "weekly_above_200ema",
    "rsi_oversold",
    "bb_lower_touch",
    "not_freefall",
    # Tier 2
    "iv_rank_high",
    "iv_percentile_high",
    "iv_above_hv",
    # Tier 3
    "option_spread_pct",
    "option_oi_min",
    "option_volume_min",
    # Tier 4 (single-ticker)
    "no_earnings_in_window",
    "min_market_cap",
    "tier_allowed",
}


def test_registry_matches_catalog() -> None:
    assert set(FILTER_REGISTRY.keys()) == EXPECTED_FILTER_IDS


def test_all_ids_returns_sorted_list() -> None:
    assert all_ids() == sorted(EXPECTED_FILTER_IDS)


@pytest.mark.parametrize("filter_id", sorted(EXPECTED_FILTER_IDS))
def test_each_id_resolves_to_class_with_matching_id(filter_id: str) -> None:
    cls = resolve(filter_id)
    assert cls.id == filter_id


def test_resolve_unknown_raises() -> None:
    with pytest.raises(UnknownFilterError):
        resolve("does_not_exist")


VALID_CATEGORIES = {"trend", "volatility", "liquidity", "event"}
VALID_PARAM_KINDS = {"number", "integer", "percent", "currency", "tier_set"}


@pytest.mark.parametrize("filter_id", sorted(EXPECTED_FILTER_IDS))
def test_each_filter_declares_catalog_metadata(filter_id: str) -> None:
    cls = resolve(filter_id)
    assert cls.label, f"{filter_id}: label must be non-empty"
    assert cls.description, f"{filter_id}: description must be non-empty"
    assert cls.category in VALID_CATEGORIES, (
        f"{filter_id}: category={cls.category!r} not in {VALID_CATEGORIES}"
    )
    assert isinstance(cls.scored, bool)
    for spec in cls.param_schema:
        assert spec.name, f"{filter_id}: param name must be non-empty"
        assert spec.label, f"{filter_id}: param label must be non-empty"
        assert spec.kind in VALID_PARAM_KINDS, (
            f"{filter_id}: param kind={spec.kind!r} not in {VALID_PARAM_KINDS}"
        )
