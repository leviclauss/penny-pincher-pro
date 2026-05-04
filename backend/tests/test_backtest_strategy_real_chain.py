"""End-to-end strategy backtest with RealChainPricer.

The goal isn't reproducing exact P&L numbers — those are dominated by
strike selection and expiration drift. Instead we assert that the simulator
*consults* options_historical when ``pricer=RealChainPricer(session)`` is
passed, by:

1. Seeding bars, indicators, and a screener config that always passes.
2. Seeding options_historical with a deliberately non-BS close (much higher
   than synthetic Black-Scholes would produce for that strike/sigma).
3. Running the backtest with the real-chain pricer.
4. Asserting at least one opened CSP's entry premium matches the seeded
   close (after slippage), not the synthetic BS price.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas_market_calendars as mcal
import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from backtest.pricing import RealChainPricer
from backtest.simulator import LEG_CSP_OPEN, StrategyParams, run_strategy_backtest
from db.models.backtest import BacktestTrade
from db.models.market import BarDaily, IndicatorDaily, OptionsHistorical, Ticker
from db.models.screener import FilterConfig

START = date(2024, 6, 3)
END = date(2024, 6, 14)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "real_chain_strategy.db"
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


def _trading_days(start: date, end: date) -> list[date]:
    schedule = mcal.get_calendar("NYSE").schedule(start_date=start, end_date=end)
    return [ts.date() for ts in schedule.index]


# Spot held flat at $100 so strike-grid selection is deterministic. The
# "real" chain seeds a *very expensive* put at strike $95 that BS at the
# same sigma would never produce — so any entry premium near our seeded
# value is unambiguous evidence the simulator read from the chain.
SPOT = 100.0
SEEDED_STRIKE = 95.0
SEEDED_PUT_CLOSE = 5.00  # vs. ~$0.20 from BS at sigma=0.30, 30 DTE, 5% OTM


def _seed_universe(session: Session) -> int:
    days = _trading_days(START, END)
    session.add(Ticker(symbol="AAA", is_active=True, is_hidden=False))
    for d in days:
        session.add(
            BarDaily(symbol="AAA", date=d, open=SPOT, high=SPOT, low=SPOT, close=SPOT, volume=1)
        )
        session.add(IndicatorDaily(symbol="AAA", date=d, rsi_14=20.0, hv_20=0.30))
    config = FilterConfig(
        name="rsi-passer",
        description="always-pass",
        config_json={
            "filters": [{"id": "rsi_oversold", "params": {"max_rsi": 50}, "required": True}],
            "scoring": {"weights": {"rsi_oversold": 1.0}},
        },
        is_active=True,
    )
    session.add(config)
    session.commit()
    return config.id


def _seed_chain(session: Session) -> None:
    """One chain row per (as_of, expiration) in the test window.

    For every trading day, expose put strike ``SEEDED_STRIKE`` expiring
    30 calendar days out, priced at ``SEEDED_PUT_CLOSE``. That's far above
    any plausible BS price for that strike/sigma combo, so an entry premium
    near it is conclusive evidence the simulator used the chain.
    """
    fetched = datetime.now(UTC)
    days = _trading_days(START, END + timedelta(days=60))
    for d in days:
        expiration = d + timedelta(days=30)
        # Snap expiration to a Friday so it matches the synthetic target
        # within select_expiration's "nearest available" logic.
        offset = (4 - expiration.weekday()) % 7
        expiration = expiration + timedelta(days=offset)
        session.add(
            OptionsHistorical(
                symbol="AAA",
                as_of=d,
                expiration=expiration,
                strike=SEEDED_STRIKE,
                option_type="put",
                close=SEEDED_PUT_CLOSE,
                fetched_at=fetched,
            )
        )
    session.commit()


def test_real_chain_premium_dominates_synthetic(session: Session) -> None:
    config_id = _seed_universe(session)
    _seed_chain(session)

    params = StrategyParams(
        starting_capital=20_000.0,
        max_concurrent_positions=1,
        dte_target=30,
        delta_target=0.30,
        slippage_per_share=0.0,  # so entry premium == real close
        fee_per_contract=0.0,
        min_dte_for_entry=1,  # short test window — accept tight DTEs
    )
    real_summary = run_strategy_backtest(
        session,
        config_id=config_id,
        start_date=START,
        end_date=END,
        params=params,
        symbols=["AAA"],
        pricer=RealChainPricer(session),
    )

    csp_open_prices = (
        session.execute(
            select(BacktestTrade.entry_price)
            .where(BacktestTrade.run_id == real_summary.run_id)
            .where(BacktestTrade.leg_type == LEG_CSP_OPEN)
        )
        .scalars()
        .all()
    )
    assert csp_open_prices, "expected at least one csp_open trade"
    # Every opened put pulled from the chain should match the seeded close.
    for price in csp_open_prices:
        assert price == pytest.approx(SEEDED_PUT_CLOSE, abs=0.01)


def test_synthetic_path_unchanged_without_pricer(session: Session) -> None:
    """Backwards-compat: omitting ``pricer`` falls through to SyntheticPricer.

    Synthetic BS for a 5%-OTM put at sigma=0.30, ~30 DTE is well under
    $1.00, so an entry premium near the seeded $5.00 would mean the chain
    leaked into the default path. It must not.
    """
    config_id = _seed_universe(session)
    _seed_chain(session)

    params = StrategyParams(
        starting_capital=20_000.0,
        max_concurrent_positions=1,
        slippage_per_share=0.0,
        fee_per_contract=0.0,
        min_dte_for_entry=1,
    )
    summary = run_strategy_backtest(
        session,
        config_id=config_id,
        start_date=START,
        end_date=END,
        params=params,
        symbols=["AAA"],
        # No pricer arg → SyntheticPricer.
    )

    csp_open_prices = (
        session.execute(
            select(BacktestTrade.entry_price)
            .where(BacktestTrade.run_id == summary.run_id)
            .where(BacktestTrade.leg_type == LEG_CSP_OPEN)
        )
        .scalars()
        .all()
    )
    assert csp_open_prices
    for price in csp_open_prices:
        assert price < SEEDED_PUT_CLOSE / 2, (
            "synthetic path should produce premiums far below the seeded chain price"
        )
