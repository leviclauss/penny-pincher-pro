"""Tests for the screener filter contract: base types, registry, loader.

Phase-1 coverage. Filter implementations get their own modules in phase 2;
here we just prove the contract works and that ``build_context`` slices
data point-in-time.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
import pytest
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from db.models.market import BarDaily, Earnings, IndicatorDaily, OptionsSnapshot
from screener.filters.base import Filter, FilterContext, FilterResult
from screener.loader import TickerNotFoundError, build_context
from screener.registry import FILTER_REGISTRY, register, resolve
from tests.fixtures.filter_ctx import make_context, make_indicators, make_ticker


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


@pytest.fixture(autouse=True)
def _isolated_registry() -> Iterator[None]:
    """Snapshot and restore FILTER_REGISTRY so tests can register freely."""
    saved = dict(FILTER_REGISTRY)
    FILTER_REGISTRY.clear()
    try:
        yield
    finally:
        FILTER_REGISTRY.clear()
        FILTER_REGISTRY.update(saved)


def test_filter_result_defaults() -> None:
    r = FilterResult(passed=True)
    assert r.passed is True
    assert r.score is None
    assert r.value is None
    assert r.reason is None


def test_register_and_resolve_round_trip() -> None:
    @register
    class _Demo:
        id: ClassVar[str] = "demo"

        def evaluate(self, ctx: FilterContext, params: dict[str, Any]) -> FilterResult:
            return FilterResult(passed=True, score=1.0)

    assert resolve("demo") is _Demo
    assert isinstance(_Demo(), Filter)


def test_register_rejects_missing_id() -> None:
    with pytest.raises(TypeError, match="non-empty class-level 'id"):

        @register
        class _NoId:
            def evaluate(self, ctx: FilterContext, params: dict[str, Any]) -> FilterResult:
                return FilterResult(passed=True)


def test_register_rejects_duplicate_id() -> None:
    @register
    class _First:
        id: ClassVar[str] = "dup"

        def evaluate(self, ctx: FilterContext, params: dict[str, Any]) -> FilterResult:
            return FilterResult(passed=True)

    with pytest.raises(ValueError, match="already registered"):

        @register
        class _Second:
            id: ClassVar[str] = "dup"

            def evaluate(self, ctx: FilterContext, params: dict[str, Any]) -> FilterResult:
                return FilterResult(passed=False)


def test_resolve_unknown_id_raises() -> None:
    with pytest.raises(KeyError, match="unknown filter id"):
        resolve("nope")


def test_make_context_defaults_are_safe() -> None:
    ctx = make_context()
    assert ctx.symbol == "TEST"
    assert ctx.options_chain is None
    assert ctx.earnings == []
    assert ctx.bars.empty
    assert pd.isna(ctx.indicators["ema_200"])


def test_make_indicators_rejects_unknown_column() -> None:
    with pytest.raises(KeyError, match="unknown indicator column"):
        make_indicators(date(2026, 5, 1), not_a_column=1.0)


def test_filter_protocol_runtime_check_on_synthetic() -> None:
    class _Pass:
        id: ClassVar[str] = "pass_thru"

        def evaluate(self, ctx: FilterContext, params: dict[str, Any]) -> FilterResult:
            return FilterResult(passed=True, score=1.0, value=ctx.symbol)

    inst = _Pass()
    assert isinstance(inst, Filter)
    result = inst.evaluate(make_context(), {})
    assert result.passed and result.value == "TEST"


def test_build_context_raises_when_ticker_missing(session: Session) -> None:
    with pytest.raises(TickerNotFoundError):
        build_context(session, "NOPE", date(2026, 5, 1))


def test_build_context_slices_bars_point_in_time(session: Session) -> None:
    session.add(make_ticker("AAA"))
    session.flush()

    days = [date(2026, 4, 28), date(2026, 4, 29), date(2026, 4, 30), date(2026, 5, 1)]
    for i, d in enumerate(days):
        session.add(
            BarDaily(
                symbol="AAA",
                date=d,
                open=100.0 + i,
                high=101.0 + i,
                low=99.0 + i,
                close=100.5 + i,
                volume=1_000_000,
            )
        )
    session.add(
        IndicatorDaily(
            symbol="AAA",
            date=date(2026, 4, 30),
            ema_200=100.0,
            rsi_14=42.0,
            iv_atm=0.30,
        )
    )
    session.add(Earnings(symbol="AAA", earnings_date=date(2026, 5, 15)))
    session.add(Earnings(symbol="AAA", earnings_date=date(2026, 4, 1)))
    session.add(
        OptionsSnapshot(
            symbol="AAA",
            expiration=date(2026, 5, 30),
            strike=100.0,
            option_type="put",
            bid=1.0,
            ask=1.1,
            delta=-0.3,
        )
    )
    session.flush()

    ctx = build_context(session, "AAA", date(2026, 4, 30))

    assert ctx.symbol == "AAA"
    assert len(ctx.bars) == 3
    assert ctx.bars.index.max() == pd.Timestamp(date(2026, 4, 30))
    assert ctx.indicators["rsi_14"] == 42.0
    assert ctx.indicators["ema_200"] == 100.0
    assert ctx.earnings == [date(2026, 5, 15)]
    assert ctx.options_chain is not None
    assert list(ctx.options_chain["strike"]) == [100.0]
    assert ctx.ticker.symbol == "AAA"


def test_build_context_returns_nan_indicators_when_row_missing(session: Session) -> None:
    session.add(make_ticker("BBB"))
    session.add(
        BarDaily(
            symbol="BBB",
            date=date(2026, 5, 1),
            open=10.0,
            high=11.0,
            low=9.0,
            close=10.5,
            volume=1,
        )
    )
    session.flush()

    ctx = build_context(session, "BBB", date(2026, 5, 1))
    assert pd.isna(ctx.indicators["ema_200"])
    assert pd.isna(ctx.indicators["iv_rank"])
    assert ctx.options_chain is None
    assert ctx.earnings == []
