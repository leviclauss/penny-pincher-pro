"""End-to-end screener pipeline tests against a migrated SQLite DB.

Covers required-filter short-circuit, optional filter behavior, scoring math,
sector-concentration cap, upsert idempotency, and skipping configs that
reference unknown filters.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from db.models.market import BarDaily, IndicatorDaily, Ticker
from db.models.screener import FilterConfig, ScreenerResult
from screener.pipeline import (
    DROPPED_BY_SECTOR_REASON,
    _compute_score,
    _parse_config,
    run_screener,
)

AS_OF = date(2024, 6, 3)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "screener.db"
    url = f"sqlite:///{db_path}"
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _seed_ticker_with_bars(
    session: Session,
    symbol: str,
    *,
    sector: str | None = "Tech",
    market_cap: float | None = 50_000_000_000.0,
    tier: int | None = 1,
    close: float = 100.0,
    indicators: dict[str, float | None] | None = None,
) -> None:
    session.add(
        Ticker(
            symbol=symbol,
            name=f"{symbol} Inc.",
            sector=sector,
            market_cap=market_cap,
            tier=tier,
            is_active=True,
            is_hidden=False,
        )
    )
    # Seed enough bars so NotFreefall etc. could run if used.
    for offset in range(10):
        d = AS_OF - timedelta(days=9 - offset)
        session.add(
            BarDaily(
                symbol=symbol,
                date=d,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1,
            )
        )
    if indicators is not None:
        session.add(
            IndicatorDaily(
                symbol=symbol,
                date=AS_OF,
                ema_20=indicators.get("ema_20"),
                ema_50=indicators.get("ema_50"),
                ema_200=indicators.get("ema_200"),
                ema_200_weekly=indicators.get("ema_200_weekly"),
                rsi_14=indicators.get("rsi_14"),
                bb_lower=indicators.get("bb_lower"),
                iv_atm=indicators.get("iv_atm"),
                iv_rank=indicators.get("iv_rank"),
                iv_percentile=indicators.get("iv_percentile"),
                hv_20=indicators.get("hv_20"),
            )
        )
    session.commit()


def _seed_config(session: Session, name: str, body: dict[str, Any]) -> int:
    config = FilterConfig(
        name=name,
        description=name,
        config_json=body,
        is_active=True,
    )
    session.add(config)
    session.commit()
    session.refresh(config)
    return config.id


def test_required_filter_short_circuits_symbol(session: Session) -> None:
    _seed_ticker_with_bars(
        session,
        "AAA",
        indicators={"rsi_14": 55.0, "ema_200": 100.0, "ema_200_weekly": 90.0},
    )
    # Required rsi_oversold @ <40 fails since rsi=55 → symbol must not pass.
    config_id = _seed_config(
        session,
        "tight",
        {
            "filters": [
                {"id": "rsi_oversold", "params": {"max_rsi": 40}, "required": True},
                {"id": "near_200ema", "params": {"max_pct": 0.05}},
            ],
            "scoring": {"weights": {"near_200ema": 1.0}},
        },
    )

    summary = run_screener(session, as_of=AS_OF)

    row = session.execute(select(ScreenerResult).where(ScreenerResult.symbol == "AAA")).scalar_one()
    assert row.passed is False
    assert row.score is None
    assert summary.rows_written == 1
    assert summary.per_config[0].config_id == config_id
    assert summary.per_config[0].symbols_passed == 0


def test_optional_filter_failure_does_not_short_circuit(session: Session) -> None:
    _seed_ticker_with_bars(
        session,
        "AAA",
        indicators={"rsi_14": 30.0, "ema_200": 200.0, "ema_200_weekly": 90.0},
    )
    # rsi_oversold required and passes; near_200ema optional and fails (close=100, ema=200).
    _seed_config(
        session,
        "lenient",
        {
            "filters": [
                {"id": "rsi_oversold", "params": {"max_rsi": 40}, "required": True},
                {"id": "near_200ema", "params": {"max_pct": 0.03}},
            ],
            "scoring": {"weights": {"rsi_oversold": 1.0}},
        },
    )

    run_screener(session, as_of=AS_OF)
    row = session.execute(select(ScreenerResult)).scalar_one()
    assert row.passed is True
    assert row.score is not None and row.score > 0.0


def test_score_uses_configured_weights() -> None:
    per_filter: dict[str, dict[str, Any]] = {
        "alpha": {"eligible": True, "score": 1.0},
        "beta": {"eligible": True, "score": 0.0},
    }
    weights = {"alpha": 3.0, "beta": 1.0}
    score = _compute_score(per_filter, weights)
    # 100 * (3*1 + 1*0) / 4 = 75.0
    assert score == pytest.approx(75.0)


def test_score_skips_ineligible_filters() -> None:
    per_filter: dict[str, dict[str, Any]] = {
        "alpha": {"eligible": True, "score": 1.0},
        "beta": {"eligible": False, "score": None},
    }
    weights = {"alpha": 1.0, "beta": 1.0}
    # beta drops out cleanly → score is fully alpha.
    assert _compute_score(per_filter, weights) == pytest.approx(100.0)


def test_score_returns_none_when_no_weighted_filters_eligible() -> None:
    per_filter: dict[str, dict[str, Any]] = {"alpha": {"eligible": False, "score": None}}
    assert _compute_score(per_filter, {"alpha": 1.0}) is None


def test_sector_concentration_drops_lowest_scoring_in_overcrowded_sector(
    session: Session,
) -> None:
    # Three Tech tickers, all qualifying; sector cap = 2 → lowest-scoring drops.
    for sym, rsi in (("AAA", 10.0), ("BBB", 20.0), ("CCC", 30.0)):
        _seed_ticker_with_bars(
            session,
            sym,
            indicators={"rsi_14": rsi, "ema_200_weekly": 90.0},
        )

    _seed_config(
        session,
        "tech-cap",
        {
            "filters": [
                {"id": "rsi_oversold", "params": {"max_rsi": 50}, "required": True},
                {"id": "sector_concentration", "params": {"max": 2}},
            ],
            "scoring": {"weights": {"rsi_oversold": 1.0}},
        },
    )

    summary = run_screener(session, as_of=AS_OF)
    assert summary.per_config[0].symbols_dropped_by_sector == 1
    passed = (
        session.execute(select(ScreenerResult).where(ScreenerResult.passed.is_(True)))
        .scalars()
        .all()
    )
    assert {r.symbol for r in passed} == {"AAA", "BBB"}

    # The dropped row records the postprocessor reason.
    ccc = session.execute(select(ScreenerResult).where(ScreenerResult.symbol == "CCC")).scalar_one()
    assert ccc.passed is False
    assert ccc.filter_results_json is not None
    assert ccc.filter_results_json["sector_concentration"]["reason"] == DROPPED_BY_SECTOR_REASON


def test_pipeline_is_idempotent(session: Session) -> None:
    _seed_ticker_with_bars(
        session,
        "AAA",
        indicators={"rsi_14": 30.0, "ema_200_weekly": 90.0},
    )
    _seed_config(
        session,
        "single",
        {
            "filters": [{"id": "rsi_oversold", "params": {"max_rsi": 40}, "required": True}],
            "scoring": {"weights": {"rsi_oversold": 1.0}},
        },
    )

    run_screener(session, as_of=AS_OF)
    first = session.execute(select(func.count()).select_from(ScreenerResult)).scalar_one()
    run_screener(session, as_of=AS_OF)
    second = session.execute(select(func.count()).select_from(ScreenerResult)).scalar_one()
    assert first == second == 1


def test_unknown_filter_id_skips_config(session: Session) -> None:
    _seed_ticker_with_bars(
        session,
        "AAA",
        indicators={"rsi_14": 30.0, "ema_200_weekly": 90.0},
    )
    _seed_config(
        session,
        "broken",
        {
            "filters": [{"id": "this_filter_does_not_exist", "required": True}],
            "scoring": {},
        },
    )

    summary = run_screener(session, as_of=AS_OF)
    assert summary.configs_run == 0
    assert summary.rows_written == 0


def test_inactive_configs_are_skipped(session: Session) -> None:
    _seed_ticker_with_bars(
        session,
        "AAA",
        indicators={"rsi_14": 30.0, "ema_200_weekly": 90.0},
    )
    config = FilterConfig(
        name="off",
        description="off",
        config_json={"filters": []},
        is_active=False,
    )
    session.add(config)
    session.commit()

    summary = run_screener(session, as_of=AS_OF)
    assert summary.configs_run == 0


def test_parse_config_extracts_sector_postprocessor() -> None:
    row = FilterConfig(
        id=1,
        name="x",
        config_json={
            "filters": [
                {"id": "rsi_oversold"},
                {"id": "sector_concentration", "params": {"max": 5}},
            ]
        },
    )
    parsed = _parse_config(row)
    assert parsed.sector_max == 5
    assert [s.id for s in parsed.filters] == ["rsi_oversold"]
