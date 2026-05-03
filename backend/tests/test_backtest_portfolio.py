"""Bookkeeping tests for ``backtest.portfolio``.

Pure dataclass logic — no DB. The strategy simulator end-to-end test exercises
the integration; these unit tests pin the per-method invariants.
"""

from __future__ import annotations

from datetime import date

from backtest.portfolio import (
    CONTRACTS_MULTIPLIER,
    MarkInputs,
    OptionPosition,
    Portfolio,
    ShareLot,
    collateral_required,
    mark_to_market,
)


def _new_portfolio(cash: float = 10_000.0) -> Portfolio:
    return Portfolio(cash=cash, starting_capital=cash)


def _new_short_put(
    *, symbol: str = "AAA", strike: float = 100.0, premium: float = 1.50
) -> OptionPosition:
    return OptionPosition(
        cycle_id=1,
        symbol=symbol,
        leg_type="short_put",
        contracts=1,
        strike=strike,
        expiration=date(2025, 2, 21),
        entry_date=date(2025, 1, 21),
        entry_premium=premium,
        fees_open=0.65,
    )


def test_collateral_locked_for_short_put() -> None:
    p = _new_portfolio()
    p.add_option(_new_short_put(strike=50.0))
    assert p.collateral_locked == 50.0 * 100
    assert p.free_cash == 10_000.0 - 50.0 * 100


def test_collateral_required_helper() -> None:
    assert collateral_required(strike=42.5, contracts=2) == 42.5 * CONTRACTS_MULTIPLIER * 2


def test_total_open_positions_counts_share_only_symbols() -> None:
    p = _new_portfolio()
    p.add_option(_new_short_put(symbol="AAA"))
    p.add_shares(
        ShareLot(
            cycle_id=2,
            symbol="BBB",
            shares=100,
            cost_basis=20.0,
            acquired_date=date(2025, 1, 10),
        )
    )
    assert p.total_open_positions() == 2


def test_cycle_id_monotonic() -> None:
    p = _new_portfolio()
    assert p.next_cycle() == 1
    assert p.next_cycle() == 2
    assert p.next_cycle_id == 3


def test_mark_to_market_with_expired_otm_put() -> None:
    p = _new_portfolio(cash=5_000.0)
    p.add_option(_new_short_put(strike=50.0, premium=1.0))
    spot = MarkInputs(spot=80.0, sigma=0.30)  # well above strike → liability ~0

    equity, unrealized = mark_to_market(
        p,
        as_of=date(2025, 2, 21),
        spot_lookup={"AAA": spot},
        risk_free_rate=0.045,
    )
    # Liability should be ~0 (expiration-day OTM put).
    assert equity > p.cash * 0.99
    # Unrealized profit on the short = the entry premium less ~0 current cost.
    assert unrealized > 50.0  # 1.0 * 100 = $100, less rounding


def test_mark_to_market_share_lot_updates_with_spot() -> None:
    p = _new_portfolio(cash=0.0)
    p.add_shares(
        ShareLot(
            cycle_id=1,
            symbol="AAA",
            shares=100,
            cost_basis=50.0,
            acquired_date=date(2025, 1, 10),
        )
    )
    spot = MarkInputs(spot=60.0, sigma=0.30)
    equity, unrealized = mark_to_market(
        p,
        as_of=date(2025, 2, 1),
        spot_lookup={"AAA": spot},
        risk_free_rate=0.045,
    )
    assert equity == 60.0 * 100  # cash 0 + shares
    assert unrealized == (60.0 - 50.0) * 100
