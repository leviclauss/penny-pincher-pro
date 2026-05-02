"""Tests for ``screener.loader.build_context`` against a real migrated SQLite.

Verifies the loader maps DB rows onto PR #10's ``FilterContext`` shape
correctly: bars sliced point-in-time, ``indicators=None`` (not a NaN-filled
Series) when the row is missing, ``options_chain`` as
``list[OptionSnapshotRecord] | None``, ``earnings`` only future-dated, and
the ``macro`` series populated from ``macro_daily``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from db.models.market import (
    BarDaily,
    Earnings,
    IndicatorDaily,
    MacroDaily,
    OptionsSnapshot,
    Ticker,
)
from screener.loader import TickerNotFoundError, build_context


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "loader.db"
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


def test_raises_when_ticker_missing(session: Session) -> None:
    with pytest.raises(TickerNotFoundError):
        build_context(session, "NOPE", date(2026, 5, 1))


def test_slices_bars_and_earnings_point_in_time(session: Session) -> None:
    session.add(Ticker(symbol="AAA", is_active=True, tier=1))
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
    session.add(Earnings(symbol="AAA", earnings_date=date(2026, 4, 1)))
    session.add(Earnings(symbol="AAA", earnings_date=date(2026, 5, 15)))
    session.add(Earnings(symbol="AAA", earnings_date=date(2026, 8, 1)))
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
    session.commit()

    ctx = build_context(session, "AAA", date(2026, 4, 30))

    assert ctx.symbol == "AAA"
    assert ctx.as_of == date(2026, 4, 30)
    assert len(ctx.bars) == 3
    assert ctx.bars.index.max() == pd.Timestamp(date(2026, 4, 30))
    assert list(ctx.bars.columns) == ["open", "high", "low", "close", "volume"]
    assert ctx.indicators is not None
    assert ctx.indicators["rsi_14"] == 42.0
    assert ctx.indicators["ema_200"] == 100.0
    assert ctx.earnings == [date(2026, 5, 15), date(2026, 8, 1)]
    assert ctx.options_chain is not None
    assert len(ctx.options_chain) == 1
    assert ctx.options_chain[0].strike == 100.0
    assert ctx.options_chain[0].option_type == "put"
    assert ctx.ticker.symbol == "AAA"


def test_returns_none_for_missing_indicators_and_chain(session: Session) -> None:
    session.add(Ticker(symbol="BBB", is_active=True))
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
    session.commit()

    ctx = build_context(session, "BBB", date(2026, 5, 1))
    assert ctx.indicators is None
    assert ctx.options_chain is None
    assert ctx.earnings == []
    assert ctx.macro is None


def test_returns_empty_bars_when_no_history(session: Session) -> None:
    session.add(Ticker(symbol="CCC", is_active=True))
    session.commit()

    ctx = build_context(session, "CCC", date(2026, 5, 1))
    assert ctx.bars.empty
    assert list(ctx.bars.columns) == ["open", "high", "low", "close", "volume"]
    assert ctx.indicators is None


def test_loads_macro_when_present(session: Session) -> None:
    session.add(Ticker(symbol="DDD", is_active=True))
    session.add(
        MacroDaily(
            date=date(2026, 5, 1),
            vix_close=18.5,
            vix_9d=17.9,
            vix_term_structure=17.9 / 18.5,
            spy_close=510.0,
            spy_ema_200=480.0,
            spy_above_200ema=True,
        )
    )
    session.commit()

    ctx = build_context(session, "DDD", date(2026, 5, 1))
    assert ctx.macro is not None
    assert ctx.macro["vix_close"] == 18.5
    assert ctx.macro["spy_above_200ema"] is True
    assert ctx.macro["vix_term_structure"] == pytest.approx(17.9 / 18.5)


def test_options_chain_preserves_all_columns(session: Session) -> None:
    session.add(Ticker(symbol="EEE", is_active=True))
    session.add(
        OptionsSnapshot(
            symbol="EEE",
            expiration=date(2026, 6, 20),
            strike=50.0,
            option_type="call",
            bid=2.0,
            ask=2.1,
            last=2.05,
            volume=None,
            open_interest=None,
            delta=0.45,
            gamma=0.02,
            theta=-0.03,
            vega=0.10,
            iv=0.28,
        )
    )
    session.commit()

    ctx = build_context(session, "EEE", date(2026, 5, 1))
    assert ctx.options_chain is not None
    rec = ctx.options_chain[0]
    assert rec.symbol == "EEE"
    assert rec.option_type == "call"
    assert rec.bid == 2.0
    assert rec.delta == 0.45
    assert rec.iv == 0.28
    assert rec.volume is None
    assert rec.open_interest is None
