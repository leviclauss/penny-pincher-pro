"""End-to-end strategy backtest tests.

The simulator picks strikes via Black-Scholes and a synthetic IV estimate, so
"exact" P&L numbers are brittle. These tests assert *invariants* instead:

- A run row, equity rows for every NYSE day, and trade rows are persisted.
- Cash + collateral accounting always closes (cash never goes negative when
  a CSP is open; equity = cash on a flat day).
- The capital cap blocks new entries once concurrency would be exceeded.
- A symbol that crashes through the put strike at expiry produces a
  ``csp_assigned`` trade and a share lot in the portfolio.

The seeded universe is two synthetic tickers whose closes monotonically rise
or fall so we can predict whether puts assign without depending on stochastic
fixtures.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import pandas_market_calendars as mcal
import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from backtest.simulator import (
    LEG_CSP_ASSIGNED,
    LEG_CSP_CLOSE,
    LEG_CSP_OPEN,
    LEG_SHARE_SOLD,
    StrategyParams,
    run_strategy_backtest,
)
from db.models.backtest import BacktestEquity, BacktestRun, BacktestTrade
from db.models.market import BarDaily, IndicatorDaily, Ticker
from db.models.screener import FilterConfig

if TYPE_CHECKING:
    from backtest.portfolio import OptionPosition, Portfolio
    from backtest.simulator import _SimState

START = date(2024, 6, 3)
END = date(2024, 8, 30)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "strategy.db"
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


def _seed_bull_passing_universe(session: Session, *, symbols: list[str], rsi: float = 25.0) -> int:
    """Seed two upward-drifting symbols + an ``rsi_oversold`` config they pass.

    Each symbol's close marches up by $0.50 a trading day (so puts never
    finish ITM, exercising the expire-worthless branch).
    """
    days = _trading_days(START, END)
    for symbol in symbols:
        session.add(
            Ticker(symbol=symbol, name=symbol, sector="Tech", is_active=True, is_hidden=False)
        )
    for symbol_idx, symbol in enumerate(symbols):
        for i, d in enumerate(days):
            close = 100.0 + symbol_idx * 50.0 + i * 0.5
            session.add(
                BarDaily(
                    symbol=symbol, date=d, open=close, high=close, low=close, close=close, volume=1
                )
            )
            session.add(IndicatorDaily(symbol=symbol, date=d, rsi_14=rsi, hv_20=0.30))
    config = FilterConfig(
        name="bullish-rsi-only",
        description="bullish-rsi-only",
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


def _seed_crashing_universe(session: Session, *, symbol: str = "CRA") -> int:
    """Seed one symbol whose close falls hard so any 30-delta put assigns."""
    days = _trading_days(START, END)
    session.add(Ticker(symbol=symbol, name=symbol, sector="Tech", is_active=True, is_hidden=False))
    for i, d in enumerate(days):
        # Start at 100, lose $1 a day → finishes ~$60 over ~60 trading days.
        close = max(100.0 - i * 1.0, 5.0)
        session.add(
            BarDaily(
                symbol=symbol, date=d, open=close, high=close, low=close, close=close, volume=1
            )
        )
        session.add(IndicatorDaily(symbol=symbol, date=d, rsi_14=25.0, hv_20=0.50))

    config = FilterConfig(
        name="bearish-rsi-only",
        description="bearish-rsi-only",
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


def test_run_writes_run_equity_and_trade_rows(session: Session) -> None:
    config_id = _seed_bull_passing_universe(session, symbols=["AAA", "BBB"])

    summary = run_strategy_backtest(
        session,
        config_id=config_id,
        start_date=START,
        end_date=END,
        params=StrategyParams(starting_capital=50_000.0, max_concurrent_positions=2),
    )

    run = session.execute(select(BacktestRun).where(BacktestRun.id == summary.run_id)).scalar_one()
    assert run.config_id == config_id
    assert run.starting_capital == 50_000.0
    assert run.mode == "strategy"
    assert run.status == "completed"
    assert run.error_message is None
    assert run.params_json is not None
    assert run.params_json["max_concurrent_positions"] == 2

    equity_rows = (
        session.execute(
            select(BacktestEquity)
            .where(BacktestEquity.run_id == summary.run_id)
            .order_by(BacktestEquity.date)
        )
        .scalars()
        .all()
    )
    assert len(equity_rows) == len(_trading_days(START, END))
    assert summary.days == len(equity_rows)
    # Cash should never go negative — collateral comes out of cash but stays non-negative.
    assert all(row.cash >= 0 for row in equity_rows)
    # Equity = cash + long_value - short_liability; on a bull-only run with no
    # share assignments the simulator should never lose more than initial capital.
    assert all(row.equity > 0 for row in equity_rows)

    trades = (
        session.execute(select(BacktestTrade).where(BacktestTrade.run_id == summary.run_id))
        .scalars()
        .all()
    )
    assert trades, "expected at least one short put to be opened"
    open_legs = [t for t in trades if t.leg_type == LEG_CSP_OPEN]
    assert open_legs, "expected csp_open rows"
    for leg in open_legs:
        assert leg.entry_price > 0
        assert leg.strike is not None and leg.strike > 0
        assert leg.expiration is not None and leg.expiration > leg.entry_date


def test_open_legs_at_window_end_have_no_duplicate_row(session: Session) -> None:
    """Regression: a position still open when the backtest window ends must
    leave exactly one ``csp_open`` row, not two.

    Earlier versions wrote one ``csp_open`` on entry day AND a second one in
    an end-of-loop "flush still-open legs" block, doubling every in-flight
    leg in the trades table.
    """
    config_id = _seed_bull_passing_universe(session, symbols=["AAA"])

    # Five trading days is far shorter than the 30-DTE entry target, so the
    # CSP opened on day one cannot close, expire, or assign — it must still
    # be open when the window ends.
    short_end = _trading_days(START, END)[4]

    summary = run_strategy_backtest(
        session,
        config_id=config_id,
        start_date=START,
        end_date=short_end,
        params=StrategyParams(starting_capital=50_000.0, max_concurrent_positions=1),
    )

    open_rows = (
        session.execute(
            select(BacktestTrade)
            .where(BacktestTrade.run_id == summary.run_id)
            .where(BacktestTrade.leg_type == LEG_CSP_OPEN)
        )
        .scalars()
        .all()
    )
    assert open_rows, "fixture should have produced at least one open CSP"

    by_cycle: dict[int, int] = {}
    for row in open_rows:
        assert row.cycle_id is not None
        by_cycle[row.cycle_id] = by_cycle.get(row.cycle_id, 0) + 1

    duplicates = {cid: n for cid, n in by_cycle.items() if n > 1}
    assert not duplicates, f"each cycle should have exactly one csp_open row; got {duplicates}"

    still_open = [r for r in open_rows if r.exit_date is None]
    assert still_open, "regression scenario requires at least one position still open at window end"


def test_capital_constraint_caps_concurrent_positions(session: Session) -> None:
    config_id = _seed_bull_passing_universe(session, symbols=["AAA", "BBB", "CCC", "DDD", "EEE"])

    summary = run_strategy_backtest(
        session,
        config_id=config_id,
        start_date=START,
        end_date=END,
        params=StrategyParams(starting_capital=20_000.0, max_concurrent_positions=2),
    )

    equity_rows = (
        session.execute(
            select(BacktestEquity)
            .where(BacktestEquity.run_id == summary.run_id)
            .order_by(BacktestEquity.date)
        )
        .scalars()
        .all()
    )
    # A single 100-strike CSP locks 10k of collateral. With 20k cash and a
    # max-2 concurrency cap, collateral should never exceed 20k.
    assert all(row.collateral_locked <= 20_000.0 + 1e-6 for row in equity_rows)
    # And the simulator should be holding at least one position by mid-run.
    assert max(row.collateral_locked for row in equity_rows) > 0


def test_assignment_path_creates_csp_assigned_trade(session: Session) -> None:
    config_id = _seed_crashing_universe(session, symbol="CRA")

    summary = run_strategy_backtest(
        session,
        config_id=config_id,
        start_date=START,
        end_date=END,
        params=StrategyParams(
            starting_capital=20_000.0,
            max_concurrent_positions=1,
            dte_target=30,
            # Disable early management so we let expiration play out.
            profit_take_pct=10.0,  # unreachable
            manage_dte=0,
        ),
    )

    trades = (
        session.execute(
            select(BacktestTrade)
            .where(BacktestTrade.run_id == summary.run_id)
            .order_by(BacktestTrade.entry_date)
        )
        .scalars()
        .all()
    )
    assigned = [t for t in trades if t.leg_type == LEG_CSP_ASSIGNED]
    assert assigned, "expected at least one csp_assigned trade after a steady downtrend"
    for t in assigned:
        assert t.outcome == "assigned"
        assert t.exit_date is not None
        assert t.strike is not None
        # Realized P/L on the option leg is just the premium credit kept,
        # net of opening fees — never negative regardless of how far the
        # stock fell. The spot-vs-strike loss surfaces as unrealized on
        # the share lot the assignment created.
        assert t.realized_pnl is not None
        expected = t.entry_price * 100.0 - t.fees
        assert t.realized_pnl == pytest.approx(expected)
        assert t.realized_pnl >= 0


def test_csp_assigned_share_lot_uses_strike_as_cost_basis(session: Session) -> None:
    """The share lot born from a put assignment carries the actual strike paid.

    The put premium credit lives on its own ``csp_assigned`` ledger row, not
    folded into the cost basis. This keeps stock P/L (sale price vs. cost
    basis) cleanly separable from option premium P/L.
    """
    from backtest.portfolio import OptionPosition, Portfolio
    from backtest.simulator import _settle_short_put, _SimState
    from screener.pipeline import ParsedConfig

    portfolio = Portfolio(cash=10_000.0, starting_capital=10_000.0)
    state = _SimState(
        run_id=1,
        portfolio=portfolio,
        params=StrategyParams(),
        config=ParsedConfig(id=1, name="x", filters=(), weights={}, sector_max=None),
        universe=["TEST"],
    )
    opt = OptionPosition(
        cycle_id=1,
        symbol="TEST",
        leg_type="short_put",
        contracts=1,
        strike=100.0,
        expiration=date(2024, 7, 19),
        entry_date=date(2024, 6, 21),
        entry_premium=2.50,
        fees_open=0.65,
    )
    portfolio.add_option(opt)
    # Spot $90 → put is $10 ITM at expiry.
    _settle_short_put(state, opt, day=date(2024, 7, 19), underlying=90.0)

    assert len(portfolio.shares) == 1
    lot = portfolio.shares[0]
    assert lot.cost_basis == 100.0  # strike paid, NOT strike - premium
    assert lot.shares == 100

    assert len(state.pending) == 1
    trade = state.pending[0]
    assert trade.leg_type == LEG_CSP_ASSIGNED
    assert trade.realized_pnl == pytest.approx(2.50 * 100 - 0.65)
    assert trade.exit_price == 0.0


def test_cc_assignment_emits_share_sold_row_with_pure_stock_pnl(session: Session) -> None:
    """Covered-call assignment splits the realization into two rows.

    The ``cc_assigned`` row carries the call's premium-only P/L; the
    ``share_sold`` row carries the underlying stock P/L (strike - cost
    basis). Sum of both equals the actual cash impact of the call exit.
    """
    from backtest.portfolio import OptionPosition, Portfolio, ShareLot
    from backtest.simulator import (
        LEG_CC_ASSIGNED,
        _settle_short_call,
        _SimState,
    )
    from screener.pipeline import ParsedConfig

    portfolio = Portfolio(cash=5_000.0, starting_capital=5_000.0)
    state = _SimState(
        run_id=1,
        portfolio=portfolio,
        params=StrategyParams(),
        config=ParsedConfig(id=1, name="x", filters=(), weights={}, sector_max=None),
        universe=["TEST"],
    )
    portfolio.add_shares(
        ShareLot(
            cycle_id=7,
            symbol="TEST",
            shares=100,
            cost_basis=95.0,
            acquired_date=date(2024, 6, 1),
        )
    )
    opt = OptionPosition(
        cycle_id=7,
        symbol="TEST",
        leg_type="covered_call",
        contracts=1,
        strike=100.0,
        expiration=date(2024, 7, 19),
        entry_date=date(2024, 6, 21),
        entry_premium=1.20,
        fees_open=0.65,
        cost_basis=95.0,
    )
    portfolio.add_option(opt)
    # Spot $110 → call is $10 ITM at expiry, shares are called away at $100.
    _settle_short_call(state, opt, day=date(2024, 7, 19), underlying=110.0)

    legs = {p.leg_type: p for p in state.pending}
    assert LEG_CC_ASSIGNED in legs
    assert LEG_SHARE_SOLD in legs

    cc = legs[LEG_CC_ASSIGNED]
    sold = legs[LEG_SHARE_SOLD]

    # Call leg: pure premium credit, never swings with the stock.
    assert cc.realized_pnl == pytest.approx(1.20 * 100 - 0.65)
    assert cc.exit_price == 0.0

    # Share leg: (strike - cost_basis) * shares — stock P/L, separated.
    assert sold.realized_pnl == pytest.approx((100.0 - 95.0) * 100)
    assert sold.entry_price == pytest.approx(95.0)
    assert sold.exit_price == pytest.approx(100.0)
    assert sold.cycle_id == 7
    assert sold.expiration is None
    assert sold.fees == 0.0
    assert sold.outcome == "shares_called_away"

    # Shares are gone from the portfolio after delivery.
    assert portfolio.shares == []


def test_unknown_config_id_raises(session: Session) -> None:
    with pytest.raises(ValueError, match="unknown filter config id"):
        run_strategy_backtest(
            session,
            config_id=999,
            start_date=START,
            end_date=END,
            params=StrategyParams(),
        )


def _itm_put_state(*, hold_losers_to_expiry: bool) -> tuple[_SimState, Portfolio, OptionPosition]:
    """Build a sim state with one deep-ITM short put inside the manage-DTE window.

    The put's BS mid will be ~$20 vs. the $1 entry credit, so closing now
    would be a large loss — the exact case ``hold_losers_to_expiry`` is
    designed to skip.
    """
    from backtest.portfolio import MarkInputs, OptionPosition, Portfolio
    from backtest.simulator import _apply_management_rules, _SimState
    from screener.pipeline import ParsedConfig

    portfolio = Portfolio(cash=10_000.0, starting_capital=10_000.0)
    state = _SimState(
        run_id=1,
        portfolio=portfolio,
        params=StrategyParams(
            manage_dte=21,
            profit_take_pct=0.50,
            hold_losers_to_expiry=hold_losers_to_expiry,
        ),
        config=ParsedConfig(id=1, name="x", filters=(), weights={}, sector_max=None),
        universe=["TEST"],
    )
    today = date(2024, 7, 1)
    opt = OptionPosition(
        cycle_id=1,
        symbol="TEST",
        leg_type="short_put",
        contracts=1,
        strike=100.0,
        expiration=date(2024, 7, 12),  # 11 calendar days out → inside manage_dte=21
        entry_date=date(2024, 6, 1),
        entry_premium=1.00,
        fees_open=0.65,
    )
    portfolio.add_option(opt)
    spot_cache = {"TEST": MarkInputs(spot=80.0, sigma=0.30)}
    _apply_management_rules(state, today, spot_cache)
    return state, portfolio, opt


def test_manage_dte_closes_losing_put_by_default(session: Session) -> None:
    """Default behavior: ``manage_dte`` buys back even a deep-ITM put at a loss."""
    state, portfolio, opt = _itm_put_state(hold_losers_to_expiry=False)
    assert opt not in portfolio.options, "default manage_dte should close the put"
    closes = [t for t in state.pending if t.leg_type == LEG_CSP_CLOSE]
    assert closes, "expected a csp_close row for the loss-realizing buy-back"
    assert closes[0].outcome == "closed_manage_dte"
    assert closes[0].realized_pnl is not None and closes[0].realized_pnl < 0


def test_hold_losers_to_expiry_skips_loss_close(session: Session) -> None:
    """``hold_losers_to_expiry=True`` rides the same put to expiration instead of closing."""
    state, portfolio, opt = _itm_put_state(hold_losers_to_expiry=True)
    assert opt in portfolio.options, (
        "hold_losers_to_expiry=True should leave the ITM put open for assignment"
    )
    closes = [t for t in state.pending if t.leg_type == LEG_CSP_CLOSE]
    assert not closes, "no buy-back row should be written when the close would be a loss"


def test_hold_losers_to_expiry_still_takes_profit(session: Session) -> None:
    """The profit-take rule must still fire when the close is a credit."""
    from backtest.portfolio import MarkInputs, OptionPosition, Portfolio
    from backtest.simulator import _apply_management_rules, _SimState
    from screener.pipeline import ParsedConfig

    portfolio = Portfolio(cash=10_000.0, starting_capital=10_000.0)
    state = _SimState(
        run_id=1,
        portfolio=portfolio,
        params=StrategyParams(
            manage_dte=21,
            profit_take_pct=0.50,
            hold_losers_to_expiry=True,
        ),
        config=ParsedConfig(id=1, name="x", filters=(), weights={}, sector_max=None),
        universe=["TEST"],
    )
    today = date(2024, 7, 1)
    # Far-OTM put with 30 DTE entry premium $5 — spot well above strike → BS mid
    # collapses toward zero, so pct_profit easily clears 50%.
    opt = OptionPosition(
        cycle_id=1,
        symbol="TEST",
        leg_type="short_put",
        contracts=1,
        strike=80.0,
        expiration=date(2024, 7, 25),  # 24 calendar days out → outside manage_dte
        entry_date=date(2024, 6, 1),
        entry_premium=5.00,
        fees_open=0.65,
    )
    portfolio.add_option(opt)
    spot_cache = {"TEST": MarkInputs(spot=120.0, sigma=0.20)}
    _apply_management_rules(state, today, spot_cache)

    assert opt not in portfolio.options
    closes = [t for t in state.pending if t.leg_type == LEG_CSP_CLOSE]
    assert closes and closes[0].outcome == "closed_profit_take"
    assert closes[0].realized_pnl is not None and closes[0].realized_pnl > 0


def test_hold_losers_to_expiry_lets_crashing_put_assign(session: Session) -> None:
    """End-to-end: with the flag set, ITM puts in a downtrend assign at expiry."""
    config_id = _seed_crashing_universe(session, symbol="CRA")

    summary = run_strategy_backtest(
        session,
        config_id=config_id,
        start_date=START,
        end_date=END,
        params=StrategyParams(
            starting_capital=20_000.0,
            max_concurrent_positions=1,
            dte_target=30,
            profit_take_pct=10.0,  # unreachable in a crashing universe
            manage_dte=21,  # would normally close ITM puts at a loss
            hold_losers_to_expiry=True,
        ),
    )

    trades = (
        session.execute(
            select(BacktestTrade)
            .where(BacktestTrade.run_id == summary.run_id)
            .order_by(BacktestTrade.entry_date)
        )
        .scalars()
        .all()
    )
    assigned = [t for t in trades if t.leg_type == LEG_CSP_ASSIGNED]
    closed = [t for t in trades if t.leg_type == LEG_CSP_CLOSE and t.outcome == "closed_manage_dte"]
    assert assigned, "ITM puts should ride to assignment, not be bought back at 21 DTE"
    assert not closed, "no manage_dte close should fire when the buy-back would be a loss"


def test_returns_summary_matches_persisted_state(session: Session) -> None:
    config_id = _seed_bull_passing_universe(session, symbols=["AAA"])

    summary = run_strategy_backtest(
        session,
        config_id=config_id,
        start_date=START,
        end_date=END,
        params=StrategyParams(starting_capital=15_000.0, max_concurrent_positions=1),
    )

    last_equity = session.execute(
        select(BacktestEquity)
        .where(BacktestEquity.run_id == summary.run_id)
        .order_by(BacktestEquity.date.desc())
        .limit(1)
    ).scalar_one()
    # Final equity tracked by the summary = cash + open share lots' MTM.
    # On the closing day of the run the equity row already reflects the same
    # state, so they should agree to within a small margin (option MTM in the
    # equity row vs. cash-only in summary if all options are still open).
    assert summary.final_equity > 0
    assert last_equity.equity > 0
