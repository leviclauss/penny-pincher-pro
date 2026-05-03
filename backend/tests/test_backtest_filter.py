"""End-to-end filter backtest tests against a migrated SQLite DB.

Seeds two symbols with controlled bars + indicator rows over a small NYSE
trading-day window so the forward-return math is exactly predictable.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pandas_market_calendars as mcal
import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from backtest.filter_backtest import LEG_TYPE, run_filter_backtest
from backtest.forward_returns import compute_forward_return
from db.models.backtest import BacktestRun, BacktestTrade
from db.models.market import BarDaily, IndicatorDaily, Ticker
from db.models.screener import FilterConfig

START = date(2024, 6, 3)
END = date(2024, 6, 7)
FORWARD_DAYS = 3
EVAL_DAYS = mcal.get_calendar("NYSE").schedule(start_date=START, end_date=END).index
BAR_DAYS = mcal.get_calendar("NYSE").schedule(start_date=START, end_date=date(2024, 6, 14)).index


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "backtest.db"
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


def _seed(session: Session) -> int:
    """Two tickers, bars on every NYSE day in BAR_DAYS, indicator rows on EVAL_DAYS.

    AAA passes ``rsi_oversold(max_rsi=40)`` (rsi=30); BBB fails (rsi=60).
    AAA close on trading-day index ``i`` is ``100 + i`` so forward returns
    over 3 trading days are (3 / (100 + i)).
    """
    session.add_all(
        [
            Ticker(symbol="AAA", name="AAA", sector="Tech", is_active=True, is_hidden=False),
            Ticker(symbol="BBB", name="BBB", sector="Tech", is_active=True, is_hidden=False),
        ]
    )
    for i, ts in enumerate(BAR_DAYS):
        d = ts.date()
        session.add(
            BarDaily(
                symbol="AAA",
                date=d,
                open=100 + i,
                high=100 + i,
                low=100 + i,
                close=100 + i,
                volume=1,
            )
        )
        session.add(
            BarDaily(
                symbol="BBB", date=d, open=50 + i, high=50 + i, low=50 + i, close=50 + i, volume=1
            )
        )
    for ts in EVAL_DAYS:
        d = ts.date()
        session.add(IndicatorDaily(symbol="AAA", date=d, rsi_14=30.0))
        session.add(IndicatorDaily(symbol="BBB", date=d, rsi_14=60.0))

    config = FilterConfig(
        name="rsi-only",
        description="rsi-only",
        config_json={
            "filters": [{"id": "rsi_oversold", "params": {"max_rsi": 40}, "required": True}],
            "scoring": {"weights": {"rsi_oversold": 1.0}},
        },
        is_active=True,
    )
    session.add(config)
    session.commit()
    session.refresh(config)
    return config.id


def test_run_writes_run_row_and_trade_per_pass(session: Session) -> None:
    config_id = _seed(session)

    run_id = run_filter_backtest(
        session,
        config_id=config_id,
        start_date=START,
        end_date=END,
        forward_days=FORWARD_DAYS,
    )

    run = session.execute(select(BacktestRun).where(BacktestRun.id == run_id)).scalar_one()
    assert run.config_id == config_id
    assert run.start_date == START
    assert run.end_date == END
    assert run.params_json is not None
    assert run.params_json["forward_days"] == FORWARD_DAYS
    assert run.params_json["symbols"] == ["AAA", "BBB"]

    trades = (
        session.execute(select(BacktestTrade).where(BacktestTrade.run_id == run_id)).scalars().all()
    )
    # AAA passes on every eval day; BBB never passes. One trade per eval day.
    assert len(trades) == len(EVAL_DAYS)
    assert {t.symbol for t in trades} == {"AAA"}
    assert {t.leg_type for t in trades} == {LEG_TYPE}
    assert all(t.outcome == "win" for t in trades)
    assert all(t.fees == 0.0 for t in trades)


def test_forward_returns_match_fixture_closes(session: Session) -> None:
    config_id = _seed(session)
    run_id = run_filter_backtest(
        session,
        config_id=config_id,
        start_date=START,
        end_date=END,
        forward_days=FORWARD_DAYS,
    )

    trades = (
        session.execute(
            select(BacktestTrade)
            .where(BacktestTrade.run_id == run_id)
            .order_by(BacktestTrade.entry_date)
        )
        .scalars()
        .all()
    )
    eval_dates = [ts.date() for ts in EVAL_DAYS]
    bar_dates = [ts.date() for ts in BAR_DAYS]
    for trade in trades:
        i = bar_dates.index(trade.entry_date)
        expected_entry = 100 + i
        expected_exit = 100 + i + FORWARD_DAYS
        expected_pct = (expected_exit - expected_entry) / expected_entry
        assert trade.entry_price == pytest.approx(expected_entry)
        assert trade.exit_price == pytest.approx(expected_exit)
        assert trade.exit_date == bar_dates[i + FORWARD_DAYS]
        assert trade.realized_pnl == pytest.approx(expected_pct * 100.0)

    assert {t.entry_date for t in trades} == set(eval_dates)


def test_compute_forward_return_returns_none_when_bars_missing(session: Session) -> None:
    _seed(session)
    # Past the seeded bar window — no exit close available.
    assert compute_forward_return(session, "AAA", date(2024, 6, 13), FORWARD_DAYS) is None
    # Symbol with no bars at all.
    assert compute_forward_return(session, "ZZZ", date(2024, 6, 3), FORWARD_DAYS) is None


def test_unknown_config_id_raises(session: Session) -> None:
    with pytest.raises(ValueError, match="unknown filter config id"):
        run_filter_backtest(
            session, config_id=999, start_date=START, end_date=END, forward_days=FORWARD_DAYS
        )
