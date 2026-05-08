"""Tests for ``backtest.coverage.options_history_coverage``.

Verifies:
- Empty DB → coverage 0, first uncovered day = first trading day, all symbols missing.
- Partial fill → ``coverage_pct`` reflects per-(symbol, day) ratio.
- Symbols filter is honored and uppercased.
- Holidays inside the window are not counted as expected coverage.
- Defaulting to active tickers when ``symbols`` is None.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from backtest.coverage import options_history_coverage
from db.models.market import OptionsHistorical, Ticker


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "coverage.db"
    url = f"sqlite:///{db_path}"
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    s.add(Ticker(symbol="AAPL", is_active=True))
    s.add(Ticker(symbol="MSFT", is_active=True))
    s.add(Ticker(symbol="OFF", is_active=False))
    s.commit()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _seed(session: Session, symbol: str, days: list[date]) -> None:
    fetched = datetime.now(UTC)
    for day in days:
        session.add(
            OptionsHistorical(
                symbol=symbol,
                as_of=day,
                expiration=date(2024, 6, 21),
                strike=170.0,
                option_type="put",
                close=1.50,
                fetched_at=fetched,
            )
        )
    session.commit()


def test_empty_db_reports_zero_coverage(session: Session) -> None:
    report = options_history_coverage(
        session,
        start=date(2024, 5, 13),
        end=date(2024, 5, 17),
        symbols=["AAPL"],
    )
    assert report.symbol_day_pairs_present == 0
    assert report.symbol_day_pairs_expected == 5  # mon-fri, no holidays
    assert report.coverage_pct == 0.0
    assert report.symbols_missing == ["AAPL"]
    assert report.first_uncovered_day == date(2024, 5, 13)


def test_partial_fill_computes_pair_ratio(session: Session) -> None:
    # 5 trading days, 2 symbols → 10 expected pairs.
    # Seed AAPL on 3 days, MSFT on 2 → 5 present.
    _seed(
        session,
        "AAPL",
        [date(2024, 5, 13), date(2024, 5, 14), date(2024, 5, 15)],
    )
    _seed(session, "MSFT", [date(2024, 5, 16), date(2024, 5, 17)])

    report = options_history_coverage(
        session,
        start=date(2024, 5, 13),
        end=date(2024, 5, 17),
        symbols=["AAPL", "MSFT"],
    )
    assert report.symbol_day_pairs_expected == 10
    assert report.symbol_day_pairs_present == 5
    assert report.coverage_pct == 0.5
    assert report.symbols_with_any_data == ["AAPL", "MSFT"]
    assert report.symbols_missing == []
    # First uncovered: 2024-05-13 is covered for AAPL but not MSFT.
    assert report.first_uncovered_day == date(2024, 5, 13)


def test_symbols_filter_uppercases_and_dedupes(session: Session) -> None:
    _seed(session, "AAPL", [date(2024, 5, 13)])
    report = options_history_coverage(
        session,
        start=date(2024, 5, 13),
        end=date(2024, 5, 13),
        symbols=["aapl", "AAPL", " aapl "],
    )
    assert report.symbols_requested == ["AAPL"]
    assert report.symbol_day_pairs_present == 1
    assert report.coverage_pct == 1.0


def test_holidays_excluded_from_expected(session: Session) -> None:
    # 2024-07-04 is a US market holiday; the window 7/3-7/5 is 2 trading days.
    _seed(session, "AAPL", [date(2024, 7, 3), date(2024, 7, 5)])
    report = options_history_coverage(
        session,
        start=date(2024, 7, 3),
        end=date(2024, 7, 5),
        symbols=["AAPL"],
    )
    assert report.trading_days == 2
    assert report.symbol_day_pairs_expected == 2
    assert report.symbol_day_pairs_present == 2
    assert report.coverage_pct == 1.0
    assert report.first_uncovered_day is None


def test_defaults_to_active_tickers(session: Session) -> None:
    _seed(session, "AAPL", [date(2024, 5, 13)])
    report = options_history_coverage(
        session,
        start=date(2024, 5, 13),
        end=date(2024, 5, 13),
    )
    # Inactive ticker "OFF" must not appear.
    assert report.symbols_requested == ["AAPL", "MSFT"]
    assert "OFF" not in report.symbols_with_any_data


def test_end_before_start_raises(session: Session) -> None:
    with pytest.raises(ValueError, match="end must be on or after start"):
        options_history_coverage(
            session,
            start=date(2024, 5, 17),
            end=date(2024, 5, 13),
            symbols=["AAPL"],
        )
