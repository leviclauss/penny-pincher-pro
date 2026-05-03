"""Portfolio state for the strategy backtest.

Tracks cash, locked collateral, open option positions, share lots, and the
ledger of completed legs. Pricing/strategy decisions live in
``simulator.py``; this module is pure bookkeeping so it stays easy to test
in isolation.

Conventions:

- Each contract represents 100 shares (``CONTRACTS_MULTIPLIER``).
- A short put locks ``strike * 100 * contracts`` cash as collateral. That
  collateral is released either when the put is closed for credit or when
  it's assigned (cash converts to shares + remaining cost basis).
- Covered calls require an underlying long share position of the same size;
  no extra collateral is locked because the shares are the collateral.
- Premiums received credit cash immediately (sale of a short option). When
  the position closes for a debit, that debit is paid from cash.
- All prices are per-share / per-contract (``mid * 100`` to convert to a
  dollar P&L per contract leg).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from backtest.pricing import price_option

CONTRACTS_MULTIPLIER = 100


@dataclass(slots=True)
class OptionPosition:
    """One open short option leg.

    ``leg_type`` is ``"short_put"`` or ``"covered_call"``. The simulator
    stores the realized leg with the canonical schema names
    (``"csp_open"``/``"csp_close"`` etc.) when it persists trade rows.
    """

    cycle_id: int
    symbol: str
    leg_type: str
    contracts: int
    strike: float
    expiration: date
    entry_date: date
    entry_premium: float  # per-share credit received
    fees_open: float
    cost_basis: float | None = None  # for covered calls, the share cost basis

    @property
    def collateral(self) -> float:
        if self.leg_type == "short_put":
            return self.strike * CONTRACTS_MULTIPLIER * self.contracts
        return 0.0

    @property
    def shares_covered(self) -> int:
        return self.contracts * CONTRACTS_MULTIPLIER


@dataclass(slots=True)
class ShareLot:
    """Long shares acquired via assignment, available to cover calls."""

    cycle_id: int
    symbol: str
    shares: int
    cost_basis: float  # per-share strike paid at assignment; the put
    # premium credit lives on its own ``csp_assigned`` ledger row, so
    # share P/L (sale proceeds vs. cost basis) is the pure stock leg.
    acquired_date: date


@dataclass(slots=True)
class Portfolio:
    cash: float
    starting_capital: float
    options: list[OptionPosition] = field(default_factory=list)
    shares: list[ShareLot] = field(default_factory=list)
    next_cycle_id: int = 1
    realized_pnl: float = 0.0

    @property
    def collateral_locked(self) -> float:
        return sum(p.collateral for p in self.options)

    @property
    def free_cash(self) -> float:
        return self.cash - self.collateral_locked

    def open_symbols(self) -> set[str]:
        return {p.symbol for p in self.options} | {s.symbol for s in self.shares}

    def shares_for_symbol(self, symbol: str) -> int:
        return sum(lot.shares for lot in self.shares if lot.symbol == symbol)

    def open_options_for(self, symbol: str) -> list[OptionPosition]:
        return [p for p in self.options if p.symbol == symbol]

    def total_open_positions(self) -> int:
        # Each open option leg counts as one position. Naked share lots from
        # assignment also count until they're covered/sold.
        symbols_with_legs = {p.symbol for p in self.options}
        symbols_with_only_shares = {
            lot.symbol for lot in self.shares if lot.symbol not in symbols_with_legs
        }
        return len(symbols_with_legs) + len(symbols_with_only_shares)

    def next_cycle(self) -> int:
        cid = self.next_cycle_id
        self.next_cycle_id += 1
        return cid

    def credit(self, amount: float) -> None:
        self.cash += amount

    def debit(self, amount: float) -> None:
        self.cash -= amount

    def add_option(self, position: OptionPosition) -> None:
        self.options.append(position)

    def remove_option(self, position: OptionPosition) -> None:
        self.options.remove(position)

    def add_shares(self, lot: ShareLot) -> None:
        self.shares.append(lot)

    def remove_shares(self, lot: ShareLot) -> None:
        self.shares.remove(lot)


@dataclass(slots=True)
class MarkInputs:
    """Per-symbol pricing inputs for end-of-day mark-to-market."""

    spot: float
    sigma: float


def mark_to_market(
    portfolio: Portfolio,
    *,
    as_of: date,
    spot_lookup: dict[str, MarkInputs],
    risk_free_rate: float,
) -> tuple[float, float]:
    """Return ``(equity, unrealized_pnl)`` for the current portfolio.

    Equity = cash + value of long shares - liability of short options.
    Unrealized P&L compares the current contract value to the original
    premium received plus the current share-lot mark vs cost basis.

    Symbols missing from ``spot_lookup`` keep their last-known value via the
    cost basis / strike — the caller is responsible for providing inputs for
    every symbol with an open leg that needs marking. We avoid importing
    pricing here is fine because ``pricing`` has no reverse dependency on
    ``portfolio``.
    """
    long_value = 0.0
    long_unrealized = 0.0
    for lot in portfolio.shares:
        inputs = spot_lookup.get(lot.symbol)
        spot = inputs.spot if inputs is not None else lot.cost_basis
        long_value += spot * lot.shares
        long_unrealized += (spot - lot.cost_basis) * lot.shares

    short_value = 0.0
    short_unrealized = 0.0
    for opt in portfolio.options:
        inputs = spot_lookup.get(opt.symbol)
        if inputs is None:
            # Without a quote, we conservatively assume the option still
            # holds its entry premium (zero unrealized contribution).
            current = opt.entry_premium
        else:
            flag = "p" if opt.leg_type == "short_put" else "c"
            quote = price_option(
                option_type=flag,
                spot=inputs.spot,
                strike=opt.strike,
                as_of=as_of,
                expiration=opt.expiration,
                sigma=inputs.sigma,
                risk_free_rate=risk_free_rate,
            )
            current = quote.mid
        liability = current * opt.shares_covered
        short_value += liability
        short_unrealized += (opt.entry_premium - current) * opt.shares_covered

    equity = portfolio.cash + long_value - short_value
    unrealized = long_unrealized + short_unrealized
    return equity, unrealized


def collateral_required(strike: float, contracts: int) -> float:
    return strike * CONTRACTS_MULTIPLIER * contracts


def lot_total_basis(lots: Iterable[ShareLot]) -> float:
    return sum(lot.shares * lot.cost_basis for lot in lots)
