"""Context builder tests — point-in-time correctness, NULL handling, no leakage."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

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
from screener.context import build_context

ENGINE_URLS: list[str] = []


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "context.db"
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


def _seed_bars(session: Session, symbol: str, bars: list[tuple[date, float]]) -> None:
    for d, close in bars:
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
    session.commit()


def test_build_context_returns_none_for_unknown_ticker(session: Session) -> None:
    assert build_context(session, "GHOST", date(2024, 6, 3)) is None


def test_build_context_filters_bars_to_as_of(session: Session) -> None:
    session.add(Ticker(symbol="AAA"))
    session.commit()
    _seed_bars(
        session,
        "AAA",
        [
            (date(2024, 6, 1), 100.0),
            (date(2024, 6, 2), 101.0),
            (date(2024, 6, 3), 102.0),
            (date(2024, 6, 4), 103.0),  # future bar — must not leak
        ],
    )

    ctx = build_context(session, "AAA", date(2024, 6, 3), include_options=False)

    assert ctx is not None
    assert len(ctx.bars) == 3
    assert ctx.latest_close() == pytest.approx(102.0)


def test_build_context_picks_latest_indicator_at_or_before(session: Session) -> None:
    session.add(Ticker(symbol="AAA"))
    session.commit()
    _seed_bars(session, "AAA", [(date(2024, 6, 3), 100.0)])
    session.add(IndicatorDaily(symbol="AAA", date=date(2024, 6, 1), ema_200=95.0, rsi_14=40.0))
    session.add(IndicatorDaily(symbol="AAA", date=date(2024, 6, 4), ema_200=99.0, rsi_14=55.0))
    session.commit()

    ctx = build_context(session, "AAA", date(2024, 6, 3), include_options=False)
    assert ctx is not None
    assert ctx.indicators is not None
    assert ctx.indicators["ema_200"] == pytest.approx(95.0)
    assert ctx.indicators["rsi_14"] == pytest.approx(40.0)


def test_build_context_returns_only_future_earnings(session: Session) -> None:
    session.add(Ticker(symbol="AAA"))
    session.commit()
    session.add(Earnings(symbol="AAA", earnings_date=date(2024, 5, 1)))  # past
    session.add(Earnings(symbol="AAA", earnings_date=date(2024, 6, 3)))  # today (included)
    session.add(Earnings(symbol="AAA", earnings_date=date(2024, 7, 1)))  # future
    session.commit()

    ctx = build_context(session, "AAA", date(2024, 6, 3), include_options=False)
    assert ctx is not None
    assert ctx.earnings == [date(2024, 6, 3), date(2024, 7, 1)]


def test_build_context_loads_macro_at_or_before(session: Session) -> None:
    session.add(Ticker(symbol="AAA"))
    session.commit()
    session.add(MacroDaily(date=date(2024, 6, 1), vix_close=18.0))
    session.add(MacroDaily(date=date(2024, 6, 5), vix_close=22.0))
    session.commit()

    ctx = build_context(session, "AAA", date(2024, 6, 3), include_options=False)
    assert ctx is not None
    assert ctx.macro is not None
    assert ctx.macro["vix_close"] == pytest.approx(18.0)


def test_build_context_skips_options_when_not_today(session: Session) -> None:
    session.add(Ticker(symbol="AAA"))
    session.commit()
    session.add(
        OptionsSnapshot(
            symbol="AAA",
            expiration=date(2024, 7, 19),
            strike=100.0,
            option_type="put",
            bid=1.0,
            ask=1.1,
        )
    )
    session.commit()

    # as_of in the past → include_options=False resolved automatically.
    ctx = build_context(session, "AAA", date(2024, 6, 3))
    assert ctx is not None
    assert ctx.options_chain is None


def test_build_context_loads_options_when_explicitly_requested(session: Session) -> None:
    session.add(Ticker(symbol="AAA"))
    session.commit()
    session.add(
        OptionsSnapshot(
            symbol="AAA",
            expiration=date(2024, 7, 19),
            strike=100.0,
            option_type="put",
            bid=1.0,
            ask=1.1,
        )
    )
    session.commit()

    ctx = build_context(session, "AAA", date(2024, 6, 3), include_options=True)
    assert ctx is not None
    assert ctx.options_chain is not None
    assert len(ctx.options_chain) == 1
    assert ctx.options_chain[0].option_type == "put"
