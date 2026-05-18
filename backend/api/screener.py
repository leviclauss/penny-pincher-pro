"""Screener resource — configs (read + write), filter catalog, per-day results.

Write endpoints (POST/PUT/DELETE/PATCH) implement PR2 of the config-UI
plan (``docs/planning/11-screener-config-ui.md``). All validation rules
live in ``_validate_write_body`` so the future config-editor can rely on
the API enforcing the same constraints it does client-side.
"""

from __future__ import annotations

from datetime import date as DateType
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from core.logging import get_logger
from db import get_session
from db.models.market import Earnings, IndicatorDaily, Ticker
from db.models.screener import FilterConfig, ScreenerResult
from ingestion.universe import get_universe_symbols
from screener.filters.base import Filter, ParamSpec
from screener.registry import FILTER_REGISTRY

log = get_logger(__name__)

# sector_concentration is a postprocessor (not in FILTER_REGISTRY) but the
# pipeline accepts it inside ``filters[]`` with a ``max`` integer param. The
# write endpoints therefore allow it as a known special-case until the
# postprocessor catalog (doc 11 "Open questions") lands.
_POSTPROCESSOR_FILTER_IDS = {"sector_concentration"}
_SECTOR_CONCENTRATION_PARAMS = {"max"}

router = APIRouter(prefix="/api/screener", tags=["screener"])


class FilterParamSchemaOut(BaseModel):
    name: str
    label: str
    kind: str
    default: float | int | list[int] | list[str]
    min: float | None = None
    max: float | None = None
    step: float | None = None
    description: str | None = None


class FilterCatalogEntry(BaseModel):
    id: str
    label: str
    description: str
    category: str
    scored: bool
    params: list[FilterParamSchemaOut]


class FilterConfigSummary(BaseModel):
    id: int
    name: str
    description: str | None
    is_active: bool
    filter_ids: list[str]
    updated_at: datetime


class FilterConfigDetail(FilterConfigSummary):
    config_json: dict[str, Any]


class ScreenerResultRow(BaseModel):
    date: DateType
    symbol: str
    config_id: int
    passed: bool
    score: float | None
    sector: str | None
    ticker_source: str
    rsi_14: float | None
    iv_rank: float | None
    iv_percentile: float | None
    near_200ema_pct: float | None
    next_earnings_date: DateType | None
    target_strike: float | None
    target_expiration: DateType | None
    target_premium: float | None
    target_delta: float | None
    annualized_return: float | None
    filter_results: dict[str, Any] | None


class ScreenerResultsResponse(BaseModel):
    date: DateType
    config_id: int
    config_name: str
    rows: list[ScreenerResultRow]


class FilterEntryIn(BaseModel):
    id: str
    params: dict[str, Any] = Field(default_factory=dict)
    required: bool = False


class ScoringIn(BaseModel):
    weights: dict[str, float] = Field(default_factory=dict)


class FilterConfigWriteIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    is_active: bool = True
    filters: list[FilterEntryIn] = Field(min_length=1)
    scoring: ScoringIn = Field(default_factory=ScoringIn)


class ActiveToggleIn(BaseModel):
    is_active: bool


@router.get("/filters", response_model=list[FilterCatalogEntry])
def list_filter_catalog() -> list[FilterCatalogEntry]:
    """Filter catalog for the config-editor UI.

    Returns one entry per registered filter, sorted by ID. The shape is
    contractually frozen by ``docs/planning/11-screener-config-ui.md``.
    """
    return [
        _catalog_entry(filter_id, FILTER_REGISTRY[filter_id])
        for filter_id in sorted(FILTER_REGISTRY)
    ]


@router.get("/configs", response_model=list[FilterConfigSummary])
def list_configs(active_only: bool = Query(default=False)) -> list[FilterConfigSummary]:
    with get_session() as session:
        stmt = select(FilterConfig).order_by(FilterConfig.id)
        if active_only:
            stmt = stmt.where(FilterConfig.is_active.is_(True))
        rows = session.execute(stmt).scalars().all()
        return [_summary_from_config(c) for c in rows]


@router.get("/configs/{config_id}", response_model=FilterConfigDetail)
def get_config(config_id: int) -> FilterConfigDetail:
    with get_session() as session:
        config = session.get(FilterConfig, config_id)
        if config is None:
            raise HTTPException(status_code=404, detail=f"config not found: {config_id}")
        return _to_detail(config)


@router.post("/configs", response_model=FilterConfigDetail, status_code=201)
def create_config(payload: FilterConfigWriteIn) -> FilterConfigDetail:
    _validate_write_body(payload)
    with get_session() as session:
        if _name_taken(session, payload.name, exclude_id=None):
            raise HTTPException(
                status_code=409, detail=f"config name already exists: {payload.name}"
            )
        config = FilterConfig(
            name=payload.name,
            description=payload.description,
            config_json=_build_config_json(payload),
            is_active=payload.is_active,
        )
        session.add(config)
        session.commit()
        session.refresh(config)
        log.info("screener.configs.create", config_id=config.id, name=config.name)
        return _to_detail(config)


@router.put("/configs/{config_id}", response_model=FilterConfigDetail)
def replace_config(config_id: int, payload: FilterConfigWriteIn) -> FilterConfigDetail:
    _validate_write_body(payload)
    with get_session() as session:
        config = session.get(FilterConfig, config_id)
        if config is None:
            raise HTTPException(status_code=404, detail=f"config not found: {config_id}")
        if _name_taken(session, payload.name, exclude_id=config_id):
            raise HTTPException(
                status_code=409, detail=f"config name already exists: {payload.name}"
            )
        config.name = payload.name
        config.description = payload.description
        config.is_active = payload.is_active
        config.config_json = _build_config_json(payload)
        session.commit()
        session.refresh(config)
        log.info("screener.configs.replace", config_id=config.id, name=config.name)
        return _to_detail(config)


@router.delete("/configs/{config_id}", status_code=204)
def delete_config(config_id: int, cascade: bool = Query(default=False)) -> Response:
    """Hard-delete a config.

    Returns 409 if any ``screener_results`` rows reference the config —
    the UI should suggest deactivation (PATCH ``/active``) instead. Pass
    ``?cascade=true`` to force-delete the config and its results.
    """
    with get_session() as session:
        config = session.get(FilterConfig, config_id)
        if config is None:
            raise HTTPException(status_code=404, detail=f"config not found: {config_id}")
        result_count = session.execute(
            select(func.count())
            .select_from(ScreenerResult)
            .where(ScreenerResult.config_id == config_id)
        ).scalar_one()
        if result_count > 0 and not cascade:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        f"config {config_id} has {result_count} screener_results rows; "
                        "deactivate via PATCH /active or pass ?cascade=true to force"
                    ),
                    "result_count": result_count,
                },
            )
        if result_count > 0:
            session.execute(delete(ScreenerResult).where(ScreenerResult.config_id == config_id))
        session.delete(config)
        session.commit()
        log.info(
            "screener.configs.delete",
            config_id=config_id,
            cascade=cascade,
            results_deleted=result_count,
        )
    return Response(status_code=204)


@router.patch("/configs/{config_id}/active", response_model=FilterConfigDetail)
def patch_active(config_id: int, payload: ActiveToggleIn) -> FilterConfigDetail:
    with get_session() as session:
        config = session.get(FilterConfig, config_id)
        if config is None:
            raise HTTPException(status_code=404, detail=f"config not found: {config_id}")
        config.is_active = payload.is_active
        session.commit()
        session.refresh(config)
        log.info(
            "screener.configs.active",
            config_id=config_id,
            is_active=config.is_active,
        )
        return _to_detail(config)


_DATE_QUERY = Query(default=None, alias="date")


@router.get("/results", response_model=ScreenerResultsResponse)
def list_results(
    config_id: int | None = Query(default=None),
    as_of: DateType | None = _DATE_QUERY,
    passed_only: bool = Query(default=True),
    ticker_source: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> ScreenerResultsResponse:
    with get_session() as session:
        config = _resolve_config(session, config_id)
        # Determine which symbol set to scope to when ticker_source is given.
        source_symbols: list[str] | None = None
        if ticker_source == "universe":
            source_symbols = get_universe_symbols(session)
        elif ticker_source == "watchlist":
            source_symbols = _watchlist_symbols(session)

        target_date = as_of or _latest_date_for(session, config.id, source_symbols)
        if target_date is None:
            return ScreenerResultsResponse(
                date=as_of or DateType.today(),
                config_id=config.id,
                config_name=config.name,
                rows=[],
            )

        stmt = (
            select(ScreenerResult)
            .where(
                ScreenerResult.config_id == config.id,
                ScreenerResult.date == target_date,
            )
            .order_by(
                desc(ScreenerResult.annualized_return),
                desc(ScreenerResult.score),
                ScreenerResult.symbol,
            )
            .limit(limit)
        )
        if passed_only:
            stmt = stmt.where(ScreenerResult.passed.is_(True))
        if source_symbols is not None:
            stmt = stmt.where(ScreenerResult.symbol.in_(source_symbols))
        rows = session.execute(stmt).scalars().all()
        if not rows:
            return ScreenerResultsResponse(
                date=target_date,
                config_id=config.id,
                config_name=config.name,
                rows=[],
            )

        symbols = [r.symbol for r in rows]
        tickers = _tickers_by_symbol(session, symbols)
        indicators = _indicators_by_symbol(session, symbols, target_date)
        next_earnings = _next_earnings_by_symbol(session, symbols, target_date)

        return ScreenerResultsResponse(
            date=target_date,
            config_id=config.id,
            config_name=config.name,
            rows=[
                _row_to_out(r, tickers.get(r.symbol), indicators.get(r.symbol), next_earnings)
                for r in rows
            ],
        )


@router.get("/results/{symbol}", response_model=list[ScreenerResultRow])
def symbol_history(
    symbol: str,
    config_id: int | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
) -> list[ScreenerResultRow]:
    sym = symbol.upper()
    cutoff = DateType.today() - timedelta(days=days)
    with get_session() as session:
        config = _resolve_config(session, config_id)
        rows = (
            session.execute(
                select(ScreenerResult)
                .where(
                    ScreenerResult.symbol == sym,
                    ScreenerResult.config_id == config.id,
                    ScreenerResult.date >= cutoff,
                )
                .order_by(ScreenerResult.date.desc())
            )
            .scalars()
            .all()
        )
        if not rows:
            return []
        ticker = session.get(Ticker, sym)
        # Indicator + earnings lookups are per-row but we only have one symbol;
        # fold them into a tiny per-row helper.
        return [_row_to_out(r, ticker, None, {}) for r in rows]


def _catalog_entry(filter_id: str, cls: type[Filter]) -> FilterCatalogEntry:
    return FilterCatalogEntry(
        id=filter_id,
        label=cls.label,
        description=cls.description,
        category=cls.category,
        scored=cls.scored,
        params=[
            FilterParamSchemaOut(
                name=spec.name,
                label=spec.label,
                kind=spec.kind,
                default=list(spec.default) if isinstance(spec.default, tuple) else spec.default,
                min=spec.min,
                max=spec.max,
                step=spec.step,
                description=spec.description,
            )
            for spec in cls.param_schema
        ],
    )


def _to_detail(config: FilterConfig) -> FilterConfigDetail:
    summary = _summary_from_config(config)
    return FilterConfigDetail(
        **summary.model_dump(),
        config_json=config.config_json or {},
    )


def _name_taken(session: Session, name: str, *, exclude_id: int | None) -> bool:
    stmt = select(FilterConfig.id).where(FilterConfig.name == name)
    if exclude_id is not None:
        stmt = stmt.where(FilterConfig.id != exclude_id)
    return session.execute(stmt).scalar_one_or_none() is not None


def _build_config_json(payload: FilterConfigWriteIn) -> dict[str, Any]:
    """Persist the same JSON shape ``seed_filter_configs.py`` writes.

    Keeping ``name`` / ``description`` mirrored inside ``config_json``
    matches the seed script and lets the editor round-trip the seed
    config without diffs.
    """
    return {
        "name": payload.name,
        "description": payload.description,
        "filters": [_filter_entry_to_json(entry) for entry in payload.filters],
        "scoring": {"weights": dict(payload.scoring.weights)},
    }


def _filter_entry_to_json(entry: FilterEntryIn) -> dict[str, Any]:
    out: dict[str, Any] = {"id": entry.id}
    if entry.params:
        out["params"] = dict(entry.params)
    if entry.required:
        out["required"] = True
    return out


def _validate_write_body(payload: FilterConfigWriteIn) -> None:
    """Reject configs the pipeline would refuse, with HTTP 400.

    Mirrors ``screener.pipeline._parse_config`` — anything that would
    cause a parse-time error there should fail here so we never persist
    a config the screener will silently skip.
    """
    seen_ids: set[str] = set()
    for entry in payload.filters:
        if entry.id in seen_ids:
            raise HTTPException(status_code=400, detail=f"duplicate filter id: {entry.id}")
        seen_ids.add(entry.id)

        if entry.id in _POSTPROCESSOR_FILTER_IDS:
            _validate_postprocessor_params(entry.id, entry.params)
            continue

        cls = FILTER_REGISTRY.get(entry.id)
        if cls is None:
            raise HTTPException(status_code=400, detail=f"unknown filter id: {entry.id}")
        _validate_params(entry.id, entry.params, cls.param_schema)

    for fid, weight in payload.scoring.weights.items():
        if fid not in seen_ids:
            raise HTTPException(
                status_code=400,
                detail=f"scoring.weights references filter not in filters[]: {fid}",
            )
        if fid in _POSTPROCESSOR_FILTER_IDS:
            raise HTTPException(
                status_code=400,
                detail=f"scoring.weights cannot weight postprocessor: {fid}",
            )
        cls = FILTER_REGISTRY[fid]
        if not cls.scored:
            raise HTTPException(
                status_code=400,
                detail=f"filter is not scored and cannot be weighted: {fid}",
            )
        if weight < 0:
            raise HTTPException(
                status_code=400, detail=f"scoring.weights[{fid}] must be non-negative"
            )


def _validate_params(
    filter_id: str,
    params: dict[str, Any],
    schema: tuple[ParamSpec, ...],
) -> None:
    by_name = {spec.name: spec for spec in schema}
    for key, value in params.items():
        spec = by_name.get(key)
        if spec is None:
            raise HTTPException(
                status_code=400,
                detail=f"filter {filter_id}: unknown param {key!r}",
            )
        _check_param_value(filter_id, spec, value)


def _check_param_value(filter_id: str, spec: ParamSpec, value: Any) -> None:
    if spec.kind == "tier_set":
        if not isinstance(value, list) or any(
            isinstance(v, bool) or not isinstance(v, int) for v in value
        ):
            raise HTTPException(
                status_code=400,
                detail=f"filter {filter_id}.{spec.name}: must be list[int]",
            )
        for v in value:
            if v not in (1, 2, 3, 4):
                raise HTTPException(
                    status_code=400,
                    detail=f"filter {filter_id}.{spec.name}: tier {v} not in [1, 2, 3, 4]",
                )
        return

    if spec.kind == "sector_set":
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            raise HTTPException(
                status_code=400,
                detail=f"filter {filter_id}.{spec.name}: must be list[str]",
            )
        return

    if spec.kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise HTTPException(
                status_code=400,
                detail=f"filter {filter_id}.{spec.name}: must be integer",
            )
        numeric: float = float(value)
    else:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise HTTPException(
                status_code=400,
                detail=f"filter {filter_id}.{spec.name}: must be number",
            )
        numeric = float(value)

    if spec.min is not None and numeric < spec.min:
        raise HTTPException(
            status_code=400,
            detail=f"filter {filter_id}.{spec.name}: {numeric} below min {spec.min}",
        )
    if spec.max is not None and numeric > spec.max:
        raise HTTPException(
            status_code=400,
            detail=f"filter {filter_id}.{spec.name}: {numeric} above max {spec.max}",
        )


def _validate_postprocessor_params(filter_id: str, params: dict[str, Any]) -> None:
    if filter_id != "sector_concentration":
        # Defensive — we only know one postprocessor today.
        raise HTTPException(status_code=400, detail=f"unknown postprocessor: {filter_id}")
    for key, value in params.items():
        if key not in _SECTOR_CONCENTRATION_PARAMS:
            raise HTTPException(
                status_code=400,
                detail=f"sector_concentration: unknown param {key!r}",
            )
        if key == "max" and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
            raise HTTPException(
                status_code=400,
                detail="sector_concentration.max must be a positive integer",
            )


def _summary_from_config(config: FilterConfig) -> FilterConfigSummary:
    raw = config.config_json or {}
    filter_ids: list[str] = []
    for entry in raw.get("filters") or []:
        if isinstance(entry, dict):
            fid = entry.get("id")
            if isinstance(fid, str):
                filter_ids.append(fid)
    return FilterConfigSummary(
        id=config.id,
        name=config.name,
        description=config.description,
        is_active=config.is_active,
        filter_ids=filter_ids,
        updated_at=config.updated_at,
    )


def _resolve_config(session: Session, config_id: int | None) -> FilterConfig:
    if config_id is not None:
        config = session.get(FilterConfig, config_id)
        if config is None:
            raise HTTPException(status_code=404, detail=f"config not found: {config_id}")
        return config
    config = session.execute(
        select(FilterConfig)
        .where(FilterConfig.is_active.is_(True))
        .order_by(FilterConfig.id)
        .limit(1)
    ).scalar_one_or_none()
    if config is None:
        raise HTTPException(status_code=404, detail="no active configs")
    return config


def _latest_date_for(
    session: Session,
    config_id: int,
    symbol_scope: list[str] | None = None,
) -> DateType | None:
    stmt = select(func.max(ScreenerResult.date)).where(ScreenerResult.config_id == config_id)
    if symbol_scope is not None:
        stmt = stmt.where(ScreenerResult.symbol.in_(symbol_scope))
    result: DateType | None = session.execute(stmt).scalar_one_or_none()
    return result


def _watchlist_symbols(session: Session) -> list[str]:
    rows = session.execute(
        select(Ticker.symbol)
        .where(Ticker.ticker_source == "watchlist", Ticker.is_active.is_(True))
        .order_by(Ticker.symbol)
    ).scalars()
    return list(rows)


def _tickers_by_symbol(session: Session, symbols: list[str]) -> dict[str, Ticker]:
    rows = session.execute(select(Ticker).where(Ticker.symbol.in_(symbols))).scalars().all()
    return {t.symbol: t for t in rows}


def _indicators_by_symbol(
    session: Session, symbols: list[str], as_of: DateType
) -> dict[str, IndicatorDaily]:
    # Latest indicator row per symbol at-or-before as_of.
    subq = (
        select(IndicatorDaily.symbol, func.max(IndicatorDaily.date).label("max_date"))
        .where(IndicatorDaily.symbol.in_(symbols), IndicatorDaily.date <= as_of)
        .group_by(IndicatorDaily.symbol)
        .subquery()
    )
    rows = (
        session.execute(
            select(IndicatorDaily).join(
                subq,
                (IndicatorDaily.symbol == subq.c.symbol) & (IndicatorDaily.date == subq.c.max_date),
            )
        )
        .scalars()
        .all()
    )
    return {ind.symbol: ind for ind in rows}


def _next_earnings_by_symbol(
    session: Session, symbols: list[str], as_of: DateType
) -> dict[str, DateType]:
    rows = session.execute(
        select(Earnings.symbol, func.min(Earnings.earnings_date))
        .where(Earnings.symbol.in_(symbols), Earnings.earnings_date >= as_of)
        .group_by(Earnings.symbol)
    ).all()
    return {row[0]: row[1] for row in rows}


def _row_to_out(
    row: ScreenerResult,
    ticker: Ticker | None,
    indicator: IndicatorDaily | None,
    next_earnings: dict[str, DateType],
) -> ScreenerResultRow:
    near_200ema_pct = _extract_filter_value(row.filter_results_json, "near_200ema")
    return ScreenerResultRow(
        date=row.date,
        symbol=row.symbol,
        config_id=row.config_id,
        passed=row.passed,
        score=row.score,
        sector=ticker.sector if ticker else None,
        ticker_source=ticker.ticker_source if ticker else "watchlist",
        rsi_14=indicator.rsi_14 if indicator else None,
        iv_rank=indicator.iv_rank if indicator else None,
        iv_percentile=indicator.iv_percentile if indicator else None,
        near_200ema_pct=near_200ema_pct,
        next_earnings_date=next_earnings.get(row.symbol),
        target_strike=row.target_strike,
        target_expiration=row.target_expiration,
        target_premium=row.target_premium,
        target_delta=row.target_delta,
        annualized_return=row.annualized_return,
        filter_results=row.filter_results_json,
    )


def _extract_filter_value(
    filter_results_json: dict[str, Any] | None, filter_id: str
) -> float | None:
    if not filter_results_json:
        return None
    entry = filter_results_json.get(filter_id)
    if not isinstance(entry, dict):
        return None
    value = entry.get("value")
    if isinstance(value, int | float):
        return float(value)
    return None
