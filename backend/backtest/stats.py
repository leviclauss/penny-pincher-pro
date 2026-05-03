"""Summary statistics for forward-return analysis."""

from __future__ import annotations

from statistics import mean, median


def hit_rate(returns: list[float]) -> float | None:
    """Fraction of returns that are positive. None if empty."""
    if not returns:
        return None
    return sum(1 for r in returns if r > 0) / len(returns)


def safe_mean(values: list[float]) -> float | None:
    """Mean of values, None if empty."""
    if not values:
        return None
    return round(mean(values), 6)


def safe_median(values: list[float]) -> float | None:
    """Median of values, None if empty."""
    if not values:
        return None
    return round(median(values), 6)
