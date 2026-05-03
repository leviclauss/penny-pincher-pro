"""Tests for the forward-return evaluator.

Uses an Alembic-migrated temp SQLite DB with deterministic price data
so return calculations are verifiable by hand.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from backtest.forward_returns import evaluate_forward_returns
from backtest.stats import hit_rate, safe_mean, safe_median
from db.models.market import BarDaily, Ticker
from db.models.screener import FilterConfig, ScreenerResult

# --- Fixtures ---


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    """Alembic-migrated temp SQLite DB session."""
    db_path = tmp_path / "forward_returns.db"
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


def _trading_days(start: date, count: int) -> list[date]:
    """Generate `count` trading days (Mon-Fri) starting from `start`."""
    days: list[date] = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:  # Mon=0 .. Fri=4
            days.append(current)
        current += timedelta(days=1)
    return days


def _seed_ticker(session: Session, symbol: str) -> None:
    """Add a ticker to the DB."""
    session.add(
        Ticker(
            symbol=symbol,
            name=f"{symbol} Inc.",
            is_active=True,
            is_hidden=False,
        )
    )
    session.flush()


def _seed_bars(
    session: Session,
    symbol: str,
    start: date,
    count: int,
    *,
    start_price: float = 100.0,
    daily_increment: float = 1.0,
) -> list[date]:
    """Seed deterministic bars: price starts at start_price, +daily_increment each trading day.

    Returns the list of trading dates seeded.
    """
    days = _trading_days(start, count)
    for i, d in enumerate(days):
        price = start_price + i * daily_increment
        session.add(
            BarDaily(
                symbol=symbol,
                date=d,
                open=price,
                high=price + 0.5,
                low=price - 0.5,
                close=price,
                volume=1_000_000,
            )
        )
    session.flush()
    return days


def _seed_config(session: Session, name: str = "test-config") -> int:
    """Create a FilterConfig and return its ID."""
    config = FilterConfig(
        name=name,
        description="Test config",
        config_json={"filters": []},
        is_active=True,
    )
    session.add(config)
    session.flush()
    session.refresh(config)
    return config.id


def _seed_screener_result(
    session: Session,
    symbol: str,
    on_date: date,
    config_id: int,
    *,
    passed: bool = True,
    score: float | None = 0.8,
) -> None:
    """Add a screener result row."""
    session.add(
        ScreenerResult(
            date=on_date,
            symbol=symbol,
            config_id=config_id,
            passed=passed,
            score=score,
        )
    )
    session.flush()


# --- Stats unit tests ---


class TestStats:
    def test_hit_rate_empty(self) -> None:
        assert hit_rate([]) is None

    def test_hit_rate_all_positive(self) -> None:
        assert hit_rate([0.01, 0.05, 0.10]) == 1.0

    def test_hit_rate_mixed(self) -> None:
        assert hit_rate([0.05, -0.02, 0.03, -0.01]) == 0.5

    def test_hit_rate_all_negative(self) -> None:
        assert hit_rate([-0.01, -0.05]) == 0.0

    def test_hit_rate_zero_not_positive(self) -> None:
        # Zero return is not positive
        assert hit_rate([0.0, 0.0]) == 0.0

    def test_safe_mean_empty(self) -> None:
        assert safe_mean([]) is None

    def test_safe_mean_values(self) -> None:
        result = safe_mean([0.1, 0.2, 0.3])
        assert result == pytest.approx(0.2, abs=1e-6)

    def test_safe_median_empty(self) -> None:
        assert safe_median([]) is None

    def test_safe_median_odd(self) -> None:
        result = safe_median([0.1, 0.3, 0.5])
        assert result == pytest.approx(0.3, abs=1e-6)

    def test_safe_median_even(self) -> None:
        result = safe_median([0.1, 0.2, 0.3, 0.4])
        assert result == pytest.approx(0.25, abs=1e-6)


# --- Forward return evaluator tests ---


class TestForwardReturns:
    def test_config_not_found_raises_value_error(self, session: Session) -> None:
        with pytest.raises(ValueError, match="FilterConfig with id=999 not found"):
            evaluate_forward_returns(
                session,
                config_id=999,
                start_date=date(2024, 1, 1),
                end_date=date(2024, 3, 31),
            )

    def test_no_screener_results_returns_empty_summary(self, session: Session) -> None:
        config_id = _seed_config(session)
        session.commit()

        summary = evaluate_forward_returns(
            session,
            config_id=config_id,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 31),
        )

        assert summary.total_picks == 0
        assert summary.picks_with_returns == 0
        assert summary.hit_rate_5d is None
        assert summary.mean_return_5d is None
        assert summary.median_return_5d is None
        assert summary.rows == []

    def test_screener_results_but_no_forward_bars(self, session: Session) -> None:
        """If there are no bars after the pick date, returns should be None."""
        _seed_ticker(session, "AAPL")
        config_id = _seed_config(session)

        pick_date = date(2024, 1, 2)
        # Only seed the bar on the pick date itself, no forward bars
        session.add(
            BarDaily(
                symbol="AAPL",
                date=pick_date,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=1_000_000,
            )
        )
        _seed_screener_result(session, "AAPL", pick_date, config_id)
        session.commit()

        summary = evaluate_forward_returns(
            session,
            config_id=config_id,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 31),
        )

        assert summary.total_picks == 1
        assert summary.picks_with_returns == 0
        assert summary.rows[0].close_on_date == 100.0
        assert summary.rows[0].return_5d is None
        assert summary.rows[0].return_10d is None
        assert summary.rows[0].return_21d is None

    def test_correct_return_calculations(self, session: Session) -> None:
        """With deterministic +$1/day bars, verify exact return values."""
        _seed_ticker(session, "AAPL")
        config_id = _seed_config(session)

        # Seed 60 trading days starting 2024-01-02 (Tuesday)
        # Price: 100, 101, 102, ... (close = 100 + i)
        start = date(2024, 1, 2)
        days = _seed_bars(session, "AAPL", start, 60, start_price=100.0, daily_increment=1.0)

        # Pick on day 0 (2024-01-02), close = 100
        pick_date = days[0]
        _seed_screener_result(session, "AAPL", pick_date, config_id)
        session.commit()

        summary = evaluate_forward_returns(
            session,
            config_id=config_id,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 31),
        )

        assert summary.total_picks == 1
        assert summary.picks_with_returns == 1

        row = summary.rows[0]
        assert row.symbol == "AAPL"
        assert row.date == pick_date
        assert row.close_on_date == 100.0

        # 5 trading days after day 0 → day 5, close = 105
        # return = (105 - 100) / 100 = 0.05
        assert row.return_5d == pytest.approx(0.05, abs=1e-6)

        # 10 trading days after day 0 → day 10, close = 110
        # return = (110 - 100) / 100 = 0.10
        assert row.return_10d == pytest.approx(0.10, abs=1e-6)

        # 21 trading days after day 0 → day 21, close = 121
        # return = (121 - 100) / 100 = 0.21
        assert row.return_21d == pytest.approx(0.21, abs=1e-6)

    def test_only_passed_results_included(self, session: Session) -> None:
        """Results with passed=False should not appear in the output."""
        _seed_ticker(session, "AAPL")
        _seed_ticker(session, "MSFT")
        config_id = _seed_config(session)

        start = date(2024, 1, 2)
        _seed_bars(session, "AAPL", start, 30, start_price=100.0)
        _seed_bars(session, "MSFT", start, 30, start_price=200.0)

        pick_date = start
        _seed_screener_result(session, "AAPL", pick_date, config_id, passed=True)
        _seed_screener_result(session, "MSFT", pick_date, config_id, passed=False)
        session.commit()

        summary = evaluate_forward_returns(
            session,
            config_id=config_id,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 31),
        )

        assert summary.total_picks == 1
        assert summary.rows[0].symbol == "AAPL"

    def test_date_range_filtering(self, session: Session) -> None:
        """Only picks within the requested date range are included."""
        _seed_ticker(session, "AAPL")
        config_id = _seed_config(session)

        start = date(2024, 1, 2)
        days = _seed_bars(session, "AAPL", start, 60, start_price=100.0)

        # Picks on day 0, day 10, day 30
        _seed_screener_result(session, "AAPL", days[0], config_id)
        _seed_screener_result(session, "AAPL", days[10], config_id)
        _seed_screener_result(session, "AAPL", days[30], config_id)
        session.commit()

        # Only request days[5] through days[15]
        summary = evaluate_forward_returns(
            session,
            config_id=config_id,
            start_date=days[5],
            end_date=days[15],
        )

        # Only the pick on days[10] falls within the range
        assert summary.total_picks == 1
        assert summary.rows[0].date == days[10]

    def test_aggregate_stats_computed_correctly(self, session: Session) -> None:
        """Verify hit rate, mean, and median across multiple picks."""
        _seed_ticker(session, "AAPL")
        _seed_ticker(session, "MSFT")
        config_id = _seed_config(session)

        start = date(2024, 1, 2)
        # AAPL: price goes up ($1/day) → positive returns
        _seed_bars(session, "AAPL", start, 60, start_price=100.0, daily_increment=1.0)
        # MSFT: price goes down ($1/day) → negative returns
        _seed_bars(session, "MSFT", start, 60, start_price=200.0, daily_increment=-1.0)

        pick_date = start
        _seed_screener_result(session, "AAPL", pick_date, config_id)
        _seed_screener_result(session, "MSFT", pick_date, config_id)
        session.commit()

        summary = evaluate_forward_returns(
            session,
            config_id=config_id,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 31),
        )

        assert summary.total_picks == 2
        assert summary.picks_with_returns == 2

        # AAPL 5d return: (105-100)/100 = 0.05
        # MSFT 5d return: (195-200)/200 = -0.025
        # Hit rate: 1/2 = 0.5
        assert summary.hit_rate_5d == pytest.approx(0.5)
        # Mean: (0.05 + (-0.025)) / 2 = 0.0125
        assert summary.mean_return_5d == pytest.approx(0.0125, abs=1e-6)
        # Median of [0.05, -0.025] = (0.05 + -0.025) / 2 = 0.0125
        assert summary.median_return_5d == pytest.approx(0.0125, abs=1e-6)

    def test_partial_forward_data(self, session: Session) -> None:
        """If only some holding periods have enough bars, partial returns are computed."""
        _seed_ticker(session, "AAPL")
        config_id = _seed_config(session)

        start = date(2024, 1, 2)
        # Only 8 trading days of forward data (enough for 5d but not 10d or 21d)
        _seed_bars(session, "AAPL", start, 9, start_price=100.0, daily_increment=1.0)

        pick_date = start
        _seed_screener_result(session, "AAPL", pick_date, config_id)
        session.commit()

        summary = evaluate_forward_returns(
            session,
            config_id=config_id,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 31),
        )

        row = summary.rows[0]
        # 5 bars after pick_date exist (days 1-8, we need 5) → return computed
        assert row.return_5d == pytest.approx(0.05, abs=1e-6)
        # Only 8 bars after pick_date, need 10 → None
        assert row.return_10d is None
        # Need 21 → None
        assert row.return_21d is None

        # picks_with_returns counts picks with at least one non-None return
        assert summary.picks_with_returns == 1

    def test_weekends_skipped_in_trading_days(self, session: Session) -> None:
        """Forward returns count trading days, not calendar days."""
        _seed_ticker(session, "AAPL")
        config_id = _seed_config(session)

        # Start on a Monday (2024-01-08)
        start = date(2024, 1, 8)
        days = _seed_bars(session, "AAPL", start, 30, start_price=100.0, daily_increment=1.0)

        # Pick on Monday (day 0)
        _seed_screener_result(session, "AAPL", days[0], config_id)
        session.commit()

        summary = evaluate_forward_returns(
            session,
            config_id=config_id,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 31),
        )

        row = summary.rows[0]
        # 5 trading days later = next Monday (skipping weekend)
        # close on day 5 = 105
        assert row.return_5d == pytest.approx(0.05, abs=1e-6)

    def test_summary_metadata(self, session: Session) -> None:
        """Summary carries config metadata and date range."""
        config_id = _seed_config(session, name="my-strategy")
        session.commit()

        summary = evaluate_forward_returns(
            session,
            config_id=config_id,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 6, 30),
        )

        assert summary.config_id == config_id
        assert summary.config_name == "my-strategy"
        assert summary.start_date == date(2024, 1, 1)
        assert summary.end_date == date(2024, 6, 30)
