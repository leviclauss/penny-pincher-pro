"""Screener orchestrator: load configs, evaluate filters, persist results.

One row per ``(date, symbol, config_id)`` lands in ``screener_results`` so
the UI can show today's candidates and backtests can replay decisions later.

Per-config algorithm:

    1. Build ``FilterContext`` for the symbol at ``as_of``.
    2. Evaluate each filter in config order. Required filters (and required
       filters that come back ``eligible=False``) short-circuit the symbol.
    3. Score = weighted average over filters whose entry in
       ``scoring.weights`` is set, divided by the sum of weights from filters
       that actually contributed (eligible + had a numeric ``score``).
       Final score is rescaled 0..100. Optional ineligible filters drop out
       cleanly rather than dragging the score down.
    4. Apply the ``sector_concentration`` post-processor — drop passers
       beyond the per-sector cap (default 3) and record the drop reason in
       ``filter_results_json``.

Strike-selection columns (``target_strike`` etc.) stay NULL — Tier-5
economics is a follow-up.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.logging import get_logger
from db.models.market import Ticker
from db.models.screener import FilterConfig, ScreenerResult
from screener.context import build_context
from screener.filters.base import FilterContext, FilterResult
from screener.registry import UnknownFilterError, resolve

log = get_logger(__name__)

DEFAULT_SECTOR_MAX = 3
DROPPED_BY_SECTOR_REASON = "dropped_by_sector_concentration"


@dataclass(frozen=True, slots=True)
class _FilterSpec:
    id: str
    required: bool
    params: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _ParsedConfig:
    id: int
    name: str
    filters: tuple[_FilterSpec, ...]
    weights: Mapping[str, float]
    sector_max: int | None  # None = sector concentration disabled


@dataclass(slots=True)
class _SymbolEvaluation:
    symbol: str
    sector: str | None
    passed: bool
    score: float | None
    filter_results: dict[str, dict[str, Any]]


@dataclass(slots=True)
class ConfigRunSummary:
    config_id: int
    config_name: str
    symbols_evaluated: int
    symbols_passed: int
    symbols_dropped_by_sector: int


@dataclass(slots=True)
class ScreenerSummary:
    as_of: date
    rows_written: int
    configs_run: int
    per_config: list[ConfigRunSummary] = field(default_factory=list)


def run_screener(
    session: Session,
    *,
    as_of: date,
    symbols: Sequence[str] | None = None,
    include_options: bool | None = None,
) -> ScreenerSummary:
    """Run every active config against the given symbols (default: all active tickers)."""
    configs = _load_active_configs(session)
    if not configs:
        log.info("screener.no_active_configs", as_of=as_of.isoformat())
        return ScreenerSummary(as_of=as_of, rows_written=0, configs_run=0)

    tickers = _load_tickers(session, symbols)
    if not tickers:
        log.info("screener.no_tickers", as_of=as_of.isoformat())
        return ScreenerSummary(as_of=as_of, rows_written=0, configs_run=len(configs))

    summary = ScreenerSummary(as_of=as_of, rows_written=0, configs_run=len(configs))

    for config in configs:
        evaluations: list[_SymbolEvaluation] = []
        for ticker in tickers:
            ctx = build_context(
                session,
                ticker.symbol,
                as_of,
                ticker=ticker,
                include_options=include_options,
            )
            if ctx is None:
                continue
            evaluations.append(_evaluate_symbol(ctx, config))

        dropped = _apply_sector_concentration(evaluations, config.sector_max)

        rows_written = _persist_evaluations(session, as_of, config.id, evaluations)
        summary.rows_written += rows_written
        summary.per_config.append(
            ConfigRunSummary(
                config_id=config.id,
                config_name=config.name,
                symbols_evaluated=len(evaluations),
                symbols_passed=sum(1 for e in evaluations if e.passed),
                symbols_dropped_by_sector=dropped,
            )
        )
        log.info(
            "screener.config.done",
            config_id=config.id,
            config_name=config.name,
            evaluated=len(evaluations),
            passed=sum(1 for e in evaluations if e.passed),
            dropped_by_sector=dropped,
        )

    log.info(
        "screener.done",
        as_of=as_of.isoformat(),
        rows=summary.rows_written,
        configs=summary.configs_run,
    )
    return summary


def _load_active_configs(session: Session) -> list[_ParsedConfig]:
    rows = (
        session.execute(
            select(FilterConfig).where(FilterConfig.is_active.is_(True)).order_by(FilterConfig.id)
        )
        .scalars()
        .all()
    )
    parsed: list[_ParsedConfig] = []
    for row in rows:
        try:
            parsed.append(_parse_config(row))
        except (UnknownFilterError, ValueError) as exc:
            log.warning(
                "screener.config.skipped",
                config_id=row.id,
                config_name=row.name,
                error=f"{type(exc).__name__}: {exc}",
            )
    return parsed


def _parse_config(row: FilterConfig) -> _ParsedConfig:
    raw = row.config_json or {}
    filters_raw = raw.get("filters") or []
    if not isinstance(filters_raw, list):
        raise ValueError("filters must be a list")
    specs: list[_FilterSpec] = []
    sector_max: int | None = None
    for entry in filters_raw:
        if not isinstance(entry, dict):
            raise ValueError("filter entry must be an object")
        fid = entry.get("id")
        if not isinstance(fid, str):
            raise ValueError("filter entry missing id")
        params = entry.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError(f"filter {fid} params must be an object")
        # sector_concentration is a postprocessor, not a Filter.
        if fid == "sector_concentration":
            sector_max = int(params.get("max", DEFAULT_SECTOR_MAX))
            continue
        # Resolve eagerly so misconfigured ids fail config load, not eval.
        resolve(fid)
        required = bool(entry.get("required", False))
        specs.append(_FilterSpec(id=fid, required=required, params=params))

    weights_raw = (raw.get("scoring") or {}).get("weights") or {}
    if not isinstance(weights_raw, dict):
        raise ValueError("scoring.weights must be an object")
    weights = {str(k): float(v) for k, v in weights_raw.items()}

    return _ParsedConfig(
        id=row.id,
        name=row.name,
        filters=tuple(specs),
        weights=weights,
        sector_max=sector_max,
    )


def _evaluate_symbol(ctx: FilterContext, config: _ParsedConfig) -> _SymbolEvaluation:
    per_filter: dict[str, dict[str, Any]] = {}
    passed = True

    for spec in config.filters:
        cls = resolve(spec.id)
        result: FilterResult = cls().evaluate(ctx, spec.params)
        per_filter[spec.id] = _result_to_dict(result, required=spec.required)
        if spec.required and (not result.eligible or not result.passed):
            passed = False
            # Keep evaluating remaining filters so the UI can show the full
            # diagnostic, but the symbol is already marked failed.

    score = _compute_score(per_filter, config.weights) if passed else None
    return _SymbolEvaluation(
        symbol=ctx.symbol,
        sector=ctx.ticker.sector,
        passed=passed,
        score=score,
        filter_results=per_filter,
    )


def _result_to_dict(result: FilterResult, *, required: bool) -> dict[str, Any]:
    return {
        "passed": result.passed,
        "eligible": result.eligible,
        "required": required,
        "score": result.score,
        "value": result.value,
        "reason": result.reason,
    }


def _compute_score(
    per_filter: Mapping[str, Mapping[str, Any]],
    weights: Mapping[str, float],
) -> float | None:
    if not weights:
        return None
    total_weight = 0.0
    weighted = 0.0
    for fid, weight in weights.items():
        entry = per_filter.get(fid)
        if entry is None:
            continue
        if not entry.get("eligible") or entry.get("score") is None:
            continue
        total_weight += weight
        weighted += weight * float(entry["score"])
    if total_weight == 0:
        return None
    return round(100.0 * weighted / total_weight, 2)


def _apply_sector_concentration(
    evaluations: list[_SymbolEvaluation], sector_max: int | None
) -> int:
    if sector_max is None or sector_max <= 0:
        return 0

    passers = [e for e in evaluations if e.passed]
    # Highest score first; unscored passers go last so they're dropped first
    # if a sector is overcrowded.
    passers.sort(key=lambda e: (e.score is None, -(e.score or 0.0), e.symbol))

    counts: dict[str, int] = {}
    dropped = 0
    for e in passers:
        bucket = e.sector or "_unknown"
        counts[bucket] = counts.get(bucket, 0) + 1
        if counts[bucket] > sector_max:
            e.passed = False
            e.score = None
            e.filter_results["sector_concentration"] = {
                "passed": False,
                "eligible": True,
                "required": True,
                "score": None,
                "value": bucket,
                "reason": DROPPED_BY_SECTOR_REASON,
            }
            dropped += 1
    return dropped


def _persist_evaluations(
    session: Session,
    as_of: date,
    config_id: int,
    evaluations: Sequence[_SymbolEvaluation],
) -> int:
    if not evaluations:
        return 0

    rows: list[dict[str, Any]] = [
        {
            "date": as_of,
            "symbol": e.symbol,
            "config_id": config_id,
            "passed": e.passed,
            "score": e.score,
            "filter_results_json": e.filter_results,
            "target_strike": None,
            "target_expiration": None,
            "target_premium": None,
            "target_delta": None,
            "annualized_return": None,
        }
        for e in evaluations
    ]

    update_cols = (
        "passed",
        "score",
        "filter_results_json",
        "target_strike",
        "target_expiration",
        "target_premium",
        "target_delta",
        "annualized_return",
    )
    stmt = sqlite_insert(ScreenerResult).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[ScreenerResult.date, ScreenerResult.symbol, ScreenerResult.config_id],
        set_={col: getattr(stmt.excluded, col) for col in update_cols},
    )
    session.execute(stmt)
    return len(rows)


def _load_tickers(session: Session, symbols: Sequence[str] | None) -> list[Ticker]:
    stmt = select(Ticker).where(Ticker.is_active.is_(True), Ticker.is_hidden.is_(False))
    if symbols is not None:
        symbol_set = {s.upper() for s in symbols}
        stmt = stmt.where(Ticker.symbol.in_(symbol_set))
    rows = session.execute(stmt.order_by(Ticker.symbol)).scalars().all()
    return cast(list[Ticker], list(rows))
