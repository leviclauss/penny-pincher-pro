"""Strategy backtest: full wheel simulation.

For every NYSE trading day in ``[start_date, end_date]`` the simulator:

1. Marks every open option leg to Black-Scholes (using ``iv_atm`` /
   ``hv_20`` from ``indicators_daily`` as the vol estimate, falling back
   to a realized-vol calc on bars).
2. Settles any leg whose expiration is on/before today: ITM short puts
   convert collateral into shares; ITM short calls deliver shares; OTM
   legs expire worthless (full premium realized).
3. Evaluates management rules — close the leg early at ``profit_take_pct``
   of max profit, or roll/close at ``manage_dte`` days to expiry. Both
   close at synthetic mid + per-contract slippage.
4. For each share lot with no covered call open, sells one CC at the
   target delta (strike floored at the share cost basis).
5. Runs the screener config against today's data, ranks passers by
   filter score, and opens new short puts for the highest-scoring
   candidates that fit within ``max_concurrent_positions`` and the
   available cash.
6. Records an equity-curve row.

Persistence:

- One ``backtest_runs`` row per invocation.
- One ``backtest_trades`` row per *closed* leg, with ``leg_type`` set to
  one of ``csp_open``/``csp_close``/``csp_assigned``/``csp_expired`` for
  puts and ``cc_open``/``cc_close``/``cc_assigned``/``cc_expired`` for
  calls. Covered-call assignments also emit a ``share_sold`` row that
  captures the per-lot share exit (``strike`` proceeds vs. share cost
  basis) — this keeps option premium P/L and underlying-stock P/L on
  separate rows. Open legs at the end of the run are also flushed
  (with no ``exit_date``) so the UI can show what's still in flight.
- One ``backtest_equity`` row per trading day.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd
import pandas_market_calendars as mcal
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from backtest.portfolio import (
    CONTRACTS_MULTIPLIER,
    MarkInputs,
    OptionPosition,
    Portfolio,
    ShareLot,
    collateral_required,
    mark_to_market,
)
from backtest.pricing import (
    DEFAULT_RISK_FREE_RATE,
    Pricer,
    SyntheticPricer,
    estimate_sigma,
    realized_vol_from_closes,
)
from core.logging import get_logger
from db.models.backtest import (
    MODE_STRATEGY,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    BacktestEquity,
    BacktestRun,
    BacktestTrade,
)
from db.models.market import BarDaily, IndicatorDaily, Ticker
from db.models.screener import FilterConfig
from screener.context import build_context
from screener.pipeline import ParsedConfig, SymbolEvaluation, evaluate_symbol, parse_config

log = get_logger(__name__)

DEFAULT_CALENDAR = "NYSE"
DEFAULT_DTE_TARGET = 30
DEFAULT_DELTA_TARGET = 0.30
DEFAULT_PROFIT_TAKE_PCT = 0.50
DEFAULT_MANAGE_DTE = 21
DEFAULT_FEE_PER_CONTRACT = 0.65
DEFAULT_SLIPPAGE_PER_SHARE = 0.02
DEFAULT_MAX_CONCURRENT = 5

LEG_CSP_OPEN = "csp_open"
LEG_CSP_CLOSE = "csp_close"
LEG_CSP_ASSIGNED = "csp_assigned"
LEG_CSP_EXPIRED = "csp_expired"
LEG_CC_OPEN = "cc_open"
LEG_CC_CLOSE = "cc_close"
LEG_CC_ASSIGNED = "cc_assigned"
LEG_CC_EXPIRED = "cc_expired"
LEG_SHARE_SOLD = "share_sold"


@dataclass(frozen=True, slots=True)
class StrategyParams:
    """Tunables for one backtest run.

    ``contracts_per_position`` is fixed at one for v0 — sizing across
    multiple contracts is a future extension. ``min_dte_for_entry`` keeps
    the simulator from picking a strike with nearly zero time value (which
    BS handles but produces brittle deltas).
    """

    starting_capital: float = 10_000.0
    max_concurrent_positions: int = DEFAULT_MAX_CONCURRENT
    dte_target: int = DEFAULT_DTE_TARGET
    delta_target: float = DEFAULT_DELTA_TARGET
    profit_take_pct: float = DEFAULT_PROFIT_TAKE_PCT
    manage_dte: int = DEFAULT_MANAGE_DTE
    fee_per_contract: float = DEFAULT_FEE_PER_CONTRACT
    slippage_per_share: float = DEFAULT_SLIPPAGE_PER_SHARE
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE
    contracts_per_position: int = 1
    min_dte_for_entry: int = 7

    def to_dict(self) -> dict[str, float | int]:
        return {
            "starting_capital": self.starting_capital,
            "max_concurrent_positions": self.max_concurrent_positions,
            "dte_target": self.dte_target,
            "delta_target": self.delta_target,
            "profit_take_pct": self.profit_take_pct,
            "manage_dte": self.manage_dte,
            "fee_per_contract": self.fee_per_contract,
            "slippage_per_share": self.slippage_per_share,
            "risk_free_rate": self.risk_free_rate,
            "contracts_per_position": self.contracts_per_position,
            "min_dte_for_entry": self.min_dte_for_entry,
        }


@dataclass(slots=True)
class _PendingTrade:
    """Buffered ``backtest_trades`` row written at the end of the run."""

    cycle_id: int
    symbol: str
    leg_type: str
    entry_date: date
    exit_date: date | None
    strike: float | None
    expiration: date | None
    entry_price: float
    exit_price: float | None
    outcome: str | None
    realized_pnl: float | None
    fees: float
    meta: dict[str, Any] | None = None


@dataclass(slots=True)
class StrategyRunSummary:
    run_id: int
    days: int = 0
    trades: int = 0
    final_equity: float = 0.0
    total_return_pct: float = 0.0
    cycles_completed: int = 0


def run_strategy_backtest(
    session: Session,
    *,
    config_id: int,
    start_date: date,
    end_date: date,
    params: StrategyParams,
    symbols: Sequence[str] | None = None,
    calendar_name: str = DEFAULT_CALENDAR,
    existing_run_id: int | None = None,
    pricer: Pricer | None = None,
) -> StrategyRunSummary:
    """Run the wheel simulator and persist results.

    See ``run_filter_backtest`` for the ``existing_run_id`` / status flow
    contract — same pre-create / completed / failed semantics.
    """
    config_row = session.execute(
        select(FilterConfig).where(FilterConfig.id == config_id)
    ).scalar_one_or_none()
    if config_row is None:
        if existing_run_id is not None:
            _mark_failed(session, existing_run_id, f"unknown filter config id: {config_id}")
        raise ValueError(f"unknown filter config id: {config_id}")
    parsed = parse_config(config_row)

    universe = _load_universe(session, symbols)
    if not universe:
        if existing_run_id is not None:
            _mark_failed(session, existing_run_id, "no active tickers in the universe")
        raise ValueError("no active tickers in the universe")

    trading_days = _trading_days(calendar_name, start_date, end_date)
    if not trading_days:
        message = f"no trading days in {start_date.isoformat()}..{end_date.isoformat()}"
        if existing_run_id is not None:
            _mark_failed(session, existing_run_id, message)
        raise ValueError(message)

    run: BacktestRun
    if existing_run_id is None:
        run = BacktestRun(
            config_id=config_id,
            mode=MODE_STRATEGY,
            status=STATUS_RUNNING,
            start_date=start_date,
            end_date=end_date,
            starting_capital=params.starting_capital,
            params_json={
                "symbols": list(universe),
                "calendar": calendar_name,
                **params.to_dict(),
            },
        )
        session.add(run)
        session.flush()
    else:
        existing = session.get(BacktestRun, existing_run_id)
        if existing is None:
            raise ValueError(f"existing_run_id not found: {existing_run_id}")
        run = existing
    run_id = run.id

    portfolio = Portfolio(cash=params.starting_capital, starting_capital=params.starting_capital)
    state = _SimState(
        run_id=run_id,
        portfolio=portfolio,
        params=params,
        config=parsed,
        universe=universe,
        pricer=pricer or SyntheticPricer(),
    )

    try:
        for day in trading_days:
            _step_day(session, state, day)

        # Flush still-open legs so the UI can render in-flight positions.
        for opt in list(portfolio.options):
            last_spot = state.last_known_spot.get(opt.symbol)
            state.pending.append(
                _PendingTrade(
                    cycle_id=opt.cycle_id,
                    symbol=opt.symbol,
                    leg_type=_open_leg_label(opt.leg_type),
                    entry_date=opt.entry_date,
                    exit_date=None,
                    strike=opt.strike,
                    expiration=opt.expiration,
                    entry_price=opt.entry_premium,
                    exit_price=None,
                    outcome="open",
                    realized_pnl=None,
                    fees=opt.fees_open,
                    meta={
                        "leg": opt.leg_type,
                        "contracts": opt.contracts,
                        "shares_covered": opt.shares_covered,
                        "strike": opt.strike,
                        "underlying_cost_basis": (
                            round(opt.cost_basis, 4) if opt.cost_basis is not None else None
                        ),
                        "premium_per_share": round(opt.entry_premium, 4),
                        "premium_total_credit": round(opt.entry_premium * opt.shares_covered, 2),
                        "fees_open": round(opt.fees_open, 2),
                        "spot_at_run_end": (round(last_spot, 4) if last_spot is not None else None),
                        "explanation": ("Position still open at end of backtest window."),
                    },
                )
            )

        _flush_trades(session, run_id, state.pending)
    except Exception as exc:
        _mark_failed(session, run_id, f"{type(exc).__name__}: {exc}")
        raise

    final_equity = portfolio.cash + sum(
        lot.shares * (state.last_known_spot.get(lot.symbol) or lot.cost_basis)
        for lot in portfolio.shares
    )
    summary = StrategyRunSummary(
        run_id=run_id,
        days=len(trading_days),
        trades=sum(1 for t in state.pending if t.exit_date is not None),
        final_equity=final_equity,
        total_return_pct=(final_equity - params.starting_capital) / params.starting_capital * 100.0,
        cycles_completed=state.cycles_completed,
    )
    run.status = STATUS_COMPLETED
    run.error_message = None
    run.params_json = {
        "symbols": list(universe),
        "calendar": calendar_name,
        **params.to_dict(),
    }
    session.commit()
    log.info(
        "backtest.strategy.summary",
        run_id=run_id,
        days=summary.days,
        trades=summary.trades,
        final_equity=round(final_equity, 2),
        return_pct=round(summary.total_return_pct, 2),
    )
    return summary


def _mark_failed(session: Session, run_id: int, message: str) -> None:
    """Roll back partial writes and flip the run row to status='failed'."""
    session.rollback()
    run = session.get(BacktestRun, run_id)
    if run is None:
        return
    run.status = STATUS_FAILED
    run.error_message = message
    session.commit()


@dataclass(slots=True)
class _SimState:
    run_id: int
    portfolio: Portfolio
    params: StrategyParams
    config: ParsedConfig
    universe: Sequence[str]
    pricer: Pricer = field(default_factory=SyntheticPricer)
    pending: list[_PendingTrade] = field(default_factory=list)
    last_known_spot: dict[str, float] = field(default_factory=dict)
    cycles_completed: int = 0


def _step_day(session: Session, state: _SimState, day: date) -> None:
    spot_cache = _load_spot_cache(session, state.universe, day)
    state.last_known_spot.update({s: m.spot for s, m in spot_cache.items()})

    _settle_expirations(state, day, spot_cache)
    _apply_management_rules(state, day, spot_cache)
    _open_covered_calls(state, day, spot_cache)
    _open_short_puts(session, state, day, spot_cache)

    equity, unrealized = mark_to_market(
        state.portfolio,
        as_of=day,
        spot_lookup=spot_cache,
        risk_free_rate=state.params.risk_free_rate,
    )
    session.add(
        BacktestEquity(
            run_id=state.run_id,
            date=day,
            equity=equity,
            cash=state.portfolio.cash,
            collateral_locked=state.portfolio.collateral_locked,
            unrealized_pnl=unrealized,
        )
    )


def _settle_expirations(
    state: _SimState,
    day: date,
    spot_cache: dict[str, MarkInputs],
) -> None:
    for opt in list(state.portfolio.options):
        if opt.expiration > day:
            continue
        spot = spot_cache.get(opt.symbol)
        underlying = spot.spot if spot is not None else state.last_known_spot.get(opt.symbol)
        if underlying is None:
            # Without a price we can't settle; defer one day at a time. This
            # is rare — bars exist for every active ticker every NYSE day.
            log.warning(
                "backtest.settle.no_price",
                run_id=state.run_id,
                symbol=opt.symbol,
                day=day.isoformat(),
            )
            continue

        if opt.leg_type == "short_put":
            _settle_short_put(state, opt, day, underlying)
        else:
            _settle_short_call(state, opt, day, underlying)


def _settle_short_put(state: _SimState, opt: OptionPosition, day: date, underlying: float) -> None:
    intrinsic = max(opt.strike - underlying, 0.0)
    state.portfolio.remove_option(opt)
    days_held = (day - opt.entry_date).days
    if intrinsic <= 0:
        # Expires worthless: collateral was held in cash; full premium kept.
        realized = opt.entry_premium * opt.shares_covered - opt.fees_open
        state.portfolio.realized_pnl += realized
        state.cycles_completed += 1
        state.pending.append(
            _PendingTrade(
                cycle_id=opt.cycle_id,
                symbol=opt.symbol,
                leg_type=LEG_CSP_EXPIRED,
                entry_date=opt.entry_date,
                exit_date=day,
                strike=opt.strike,
                expiration=opt.expiration,
                entry_price=opt.entry_premium,
                exit_price=0.0,
                outcome="win",
                realized_pnl=realized,
                fees=opt.fees_open,
                meta={
                    "leg": "short_put",
                    "contracts": opt.contracts,
                    "shares_covered": opt.shares_covered,
                    "spot_at_exit": round(underlying, 4),
                    "strike": opt.strike,
                    "intrinsic_at_exit": 0.0,
                    "premium_per_share": round(opt.entry_premium, 4),
                    "premium_total_credit": round(opt.entry_premium * opt.shares_covered, 2),
                    "fees_open": round(opt.fees_open, 2),
                    "fees_close": 0.0,
                    "days_held": days_held,
                    "pnl_breakdown": {
                        "premium_kept": round(opt.entry_premium * opt.shares_covered, 2),
                        "fees": round(-opt.fees_open, 2),
                        "net": round(realized, 2),
                    },
                    "explanation": ("Put expired OTM; full premium kept, collateral released."),
                },
            )
        )
        return

    # ITM at expiry → assigned. Pay strike * 100, receive shares at the
    # actual strike paid; the put premium stays in cash where it was
    # credited at open and is reported as this leg's realized P/L. Any
    # spot-vs-strike loss surfaces as unrealized on the share lot until
    # the shares are eventually sold (CC assignment or close).
    cost = opt.strike * opt.shares_covered
    state.portfolio.debit(cost)
    state.portfolio.add_shares(
        ShareLot(
            cycle_id=opt.cycle_id,
            symbol=opt.symbol,
            shares=opt.shares_covered,
            cost_basis=opt.strike,
            acquired_date=day,
        )
    )
    realized = opt.entry_premium * opt.shares_covered - opt.fees_open
    state.portfolio.realized_pnl += realized
    paper_loss_on_shares = (underlying - opt.strike) * opt.shares_covered
    state.pending.append(
        _PendingTrade(
            cycle_id=opt.cycle_id,
            symbol=opt.symbol,
            leg_type=LEG_CSP_ASSIGNED,
            entry_date=opt.entry_date,
            exit_date=day,
            strike=opt.strike,
            expiration=opt.expiration,
            entry_price=opt.entry_premium,
            exit_price=0.0,
            outcome="assigned",
            realized_pnl=realized,
            fees=opt.fees_open,
            meta={
                "leg": "short_put",
                "contracts": opt.contracts,
                "shares_covered": opt.shares_covered,
                "spot_at_exit": round(underlying, 4),
                "strike": opt.strike,
                "intrinsic_at_exit": round(intrinsic, 4),
                "premium_per_share": round(opt.entry_premium, 4),
                "premium_total_credit": round(opt.entry_premium * opt.shares_covered, 2),
                "fees_open": round(opt.fees_open, 2),
                "fees_close": 0.0,
                "days_held": days_held,
                "shares_acquired": opt.shares_covered,
                "share_cost_basis": opt.strike,
                "share_unrealized_pnl_at_assignment": round(paper_loss_on_shares, 2),
                "pnl_breakdown": {
                    "premium_kept": round(opt.entry_premium * opt.shares_covered, 2),
                    "fees": round(-opt.fees_open, 2),
                    "net": round(realized, 2),
                },
                "explanation": (
                    "Put assigned. This row records option premium kept only. "
                    f"Shares acquired at strike ${opt.strike:.2f}; spot was "
                    f"${underlying:.2f} so unrealized share P/L is "
                    f"${paper_loss_on_shares:.2f} until shares exit "
                    "(see future share_sold row)."
                ),
            },
        )
    )


def _settle_short_call(state: _SimState, opt: OptionPosition, day: date, underlying: float) -> None:
    intrinsic = max(underlying - opt.strike, 0.0)
    state.portfolio.remove_option(opt)
    days_held = (day - opt.entry_date).days
    if intrinsic <= 0:
        realized = opt.entry_premium * opt.shares_covered - opt.fees_open
        state.portfolio.realized_pnl += realized
        state.pending.append(
            _PendingTrade(
                cycle_id=opt.cycle_id,
                symbol=opt.symbol,
                leg_type=LEG_CC_EXPIRED,
                entry_date=opt.entry_date,
                exit_date=day,
                strike=opt.strike,
                expiration=opt.expiration,
                entry_price=opt.entry_premium,
                exit_price=0.0,
                outcome="win",
                realized_pnl=realized,
                fees=opt.fees_open,
                meta={
                    "leg": "covered_call",
                    "contracts": opt.contracts,
                    "shares_covered": opt.shares_covered,
                    "spot_at_exit": round(underlying, 4),
                    "strike": opt.strike,
                    "intrinsic_at_exit": 0.0,
                    "underlying_cost_basis": (
                        round(opt.cost_basis, 4) if opt.cost_basis is not None else None
                    ),
                    "premium_per_share": round(opt.entry_premium, 4),
                    "premium_total_credit": round(opt.entry_premium * opt.shares_covered, 2),
                    "fees_open": round(opt.fees_open, 2),
                    "fees_close": 0.0,
                    "days_held": days_held,
                    "pnl_breakdown": {
                        "premium_kept": round(opt.entry_premium * opt.shares_covered, 2),
                        "fees": round(-opt.fees_open, 2),
                        "net": round(realized, 2),
                    },
                    "explanation": ("Call expired OTM; full premium kept, shares retained."),
                },
            )
        )
        return

    # ITM call → shares are called away at strike. Two ledger events:
    # (1) the call leg ends with no exit debit (shares were delivered, not
    #     bought back), so its realized P/L is the original premium credit
    #     net of fees;
    # (2) the share lot exits at ``strike`` against its cost basis — that
    #     is the only term that legitimately swings with the underlying,
    #     and it is recorded on its own ``share_sold`` row.
    lots_to_sell = _take_lots(state.portfolio.shares, opt.symbol, opt.shares_covered)
    proceeds = opt.strike * opt.shares_covered
    state.portfolio.credit(proceeds)
    shares_sold = sum(lot.shares for lot in lots_to_sell)
    weighted_basis = (
        sum(lot.shares * lot.cost_basis for lot in lots_to_sell) / shares_sold
        if shares_sold
        else 0.0
    )
    earliest_acquired = min((lot.acquired_date for lot in lots_to_sell), default=opt.entry_date)
    share_realized = (opt.strike - weighted_basis) * shares_sold
    lot_breakdown = [
        {
            "cycle_id": lot.cycle_id,
            "shares": lot.shares,
            "cost_basis": round(lot.cost_basis, 4),
            "acquired_date": lot.acquired_date.isoformat(),
        }
        for lot in lots_to_sell
    ]
    for lot in lots_to_sell:
        state.portfolio.remove_shares(lot)
    option_realized = opt.entry_premium * opt.shares_covered - opt.fees_open
    state.portfolio.realized_pnl += option_realized + share_realized
    state.cycles_completed += 1
    state.pending.append(
        _PendingTrade(
            cycle_id=opt.cycle_id,
            symbol=opt.symbol,
            leg_type=LEG_CC_ASSIGNED,
            entry_date=opt.entry_date,
            exit_date=day,
            strike=opt.strike,
            expiration=opt.expiration,
            entry_price=opt.entry_premium,
            exit_price=0.0,
            outcome="assigned",
            realized_pnl=option_realized,
            fees=opt.fees_open,
            meta={
                "leg": "covered_call",
                "contracts": opt.contracts,
                "shares_covered": opt.shares_covered,
                "spot_at_exit": round(underlying, 4),
                "strike": opt.strike,
                "intrinsic_at_exit": round(intrinsic, 4),
                "underlying_cost_basis": (
                    round(opt.cost_basis, 4) if opt.cost_basis is not None else None
                ),
                "premium_per_share": round(opt.entry_premium, 4),
                "premium_total_credit": round(opt.entry_premium * opt.shares_covered, 2),
                "fees_open": round(opt.fees_open, 2),
                "fees_close": 0.0,
                "days_held": days_held,
                "shares_called_away": shares_sold,
                "pnl_breakdown": {
                    "premium_kept": round(opt.entry_premium * opt.shares_covered, 2),
                    "fees": round(-opt.fees_open, 2),
                    "net": round(option_realized, 2),
                },
                "explanation": (
                    "Call assigned. This row records option premium kept only. "
                    f"Shares were called away at strike ${opt.strike:.2f}; "
                    "stock P/L (vs. cost basis) is on the paired share_sold row."
                ),
            },
        )
    )
    state.pending.append(
        _PendingTrade(
            cycle_id=opt.cycle_id,
            symbol=opt.symbol,
            leg_type=LEG_SHARE_SOLD,
            entry_date=earliest_acquired,
            exit_date=day,
            strike=opt.strike,
            expiration=None,
            entry_price=weighted_basis,
            exit_price=opt.strike,
            outcome="shares_called_away",
            realized_pnl=share_realized,
            fees=0.0,
            meta={
                "leg": "shares",
                "shares_sold": shares_sold,
                "weighted_cost_basis": round(weighted_basis, 4),
                "sale_price": opt.strike,
                "earliest_acquired_date": earliest_acquired.isoformat(),
                "days_held": (day - earliest_acquired).days,
                "lots": lot_breakdown,
                "pnl_breakdown": {
                    "proceeds": round(opt.strike * shares_sold, 2),
                    "cost_basis_total": round(weighted_basis * shares_sold, 2),
                    "net": round(share_realized, 2),
                },
                "explanation": (
                    f"Shares delivered at strike ${opt.strike:.2f} against weighted "
                    f"basis ${weighted_basis:.2f}. Net = "
                    f"(${opt.strike:.2f} - ${weighted_basis:.2f}) * {shares_sold} "
                    f"= ${share_realized:.2f}."
                ),
            },
        )
    )


def _apply_management_rules(state: _SimState, day: date, spot_cache: dict[str, MarkInputs]) -> None:
    for opt in list(state.portfolio.options):
        spot = spot_cache.get(opt.symbol)
        if spot is None:
            continue
        flag = "p" if opt.leg_type == "short_put" else "c"
        quote = state.pricer.price_option(
            symbol=opt.symbol,
            as_of=day,
            option_type=flag,
            spot=spot.spot,
            strike=opt.strike,
            expiration=opt.expiration,
            sigma=spot.sigma,
            risk_free_rate=state.params.risk_free_rate,
        )
        # Realised credit if we close at this mid (less per-share slippage paid).
        close_cost = quote.mid + state.params.slippage_per_share
        # pct_max_profit = 1 - (current_cost / entry_premium); >= profit_take.
        pct_profit = 1.0 - (close_cost / opt.entry_premium) if opt.entry_premium > 0 else 0.0

        days_to_expiry = (opt.expiration - day).days
        rule: str | None = None
        if pct_profit >= state.params.profit_take_pct:
            rule = "profit_take"
        elif days_to_expiry <= state.params.manage_dte:
            rule = "manage_dte"
        if rule is None:
            continue
        _close_option_for_credit(
            state,
            opt,
            day,
            close_cost,
            rule,
            spot=spot.spot,
            sigma=spot.sigma,
            quote_mid=quote.mid,
        )


def _close_option_for_credit(
    state: _SimState,
    opt: OptionPosition,
    day: date,
    close_cost: float,
    rule: str,
    *,
    spot: float,
    sigma: float,
    quote_mid: float,
) -> None:
    state.portfolio.remove_option(opt)
    fee_close = state.params.fee_per_contract * opt.contracts
    debit = close_cost * opt.shares_covered
    state.portfolio.debit(debit + fee_close)
    realized = (opt.entry_premium - close_cost) * opt.shares_covered - opt.fees_open - fee_close
    state.portfolio.realized_pnl += realized

    if opt.leg_type == "short_put":
        leg_label = LEG_CSP_CLOSE
        if state.portfolio.shares_for_symbol(opt.symbol) == 0:
            state.cycles_completed += 1
    else:
        leg_label = LEG_CC_CLOSE

    days_held = (day - opt.entry_date).days
    days_to_expiry_remaining = (opt.expiration - day).days
    slippage_total = state.params.slippage_per_share * opt.shares_covered

    state.pending.append(
        _PendingTrade(
            cycle_id=opt.cycle_id,
            symbol=opt.symbol,
            leg_type=leg_label,
            entry_date=opt.entry_date,
            exit_date=day,
            strike=opt.strike,
            expiration=opt.expiration,
            entry_price=opt.entry_premium,
            exit_price=close_cost,
            outcome=f"closed_{rule}",
            realized_pnl=realized,
            fees=opt.fees_open + fee_close,
            meta={
                "leg": opt.leg_type,
                "contracts": opt.contracts,
                "shares_covered": opt.shares_covered,
                "spot_at_exit": round(spot, 4),
                "sigma_used": round(sigma, 6),
                "strike": opt.strike,
                "underlying_cost_basis": (
                    round(opt.cost_basis, 4) if opt.cost_basis is not None else None
                ),
                "premium_per_share": round(opt.entry_premium, 4),
                "premium_total_credit": round(opt.entry_premium * opt.shares_covered, 2),
                "close_quote_mid": round(quote_mid, 4),
                "close_slippage_per_share": state.params.slippage_per_share,
                "close_cost_per_share": round(close_cost, 4),
                "close_total_debit": round(close_cost * opt.shares_covered, 2),
                "fees_open": round(opt.fees_open, 2),
                "fees_close": round(fee_close, 2),
                "slippage_total": round(slippage_total, 2),
                "days_held": days_held,
                "days_to_expiry_at_close": days_to_expiry_remaining,
                "rule_triggered": rule,
                "pnl_breakdown": {
                    "premium_received": round(opt.entry_premium * opt.shares_covered, 2),
                    "buyback_cost": round(-close_cost * opt.shares_covered, 2),
                    "fees": round(-(opt.fees_open + fee_close), 2),
                    "net": round(realized, 2),
                },
                "explanation": (
                    f"Closed for {rule}. Bought back at ${close_cost:.4f}/sh "
                    f"(quote mid ${quote_mid:.4f} + "
                    f"${state.params.slippage_per_share:.2f} slippage); "
                    f"original credit was ${opt.entry_premium:.4f}/sh. "
                    f"Net = (${opt.entry_premium:.4f} - ${close_cost:.4f}) * "
                    f"{opt.shares_covered} - ${opt.fees_open + fee_close:.2f} fees "
                    f"= ${realized:.2f}."
                ),
            },
        )
    )


def _open_covered_calls(
    state: _SimState,
    day: date,
    spot_cache: dict[str, MarkInputs],
) -> None:
    for symbol in {lot.symbol for lot in state.portfolio.shares}:
        if any(
            opt.symbol == symbol and opt.leg_type == "covered_call"
            for opt in state.portfolio.options
        ):
            continue
        spot = spot_cache.get(symbol)
        if spot is None:
            continue
        lots = [lot for lot in state.portfolio.shares if lot.symbol == symbol]
        total_shares = sum(lot.shares for lot in lots)
        contracts = total_shares // CONTRACTS_MULTIPLIER
        if contracts <= 0:
            continue
        weighted_basis = (
            sum(lot.shares * lot.cost_basis for lot in lots) / total_shares
            if total_shares
            else spot.spot
        )
        expiration = state.pricer.select_expiration(
            symbol=symbol, as_of=day, dte_target=state.params.dte_target
        )
        days_to_expiry = (expiration - day).days
        if days_to_expiry < state.params.min_dte_for_entry:
            continue
        strike = state.pricer.select_call_strike(
            symbol=symbol,
            as_of=day,
            spot=spot.spot,
            cost_basis=weighted_basis,
            target_delta=state.params.delta_target,
            expiration=expiration,
            sigma=spot.sigma,
            days_to_expiry=days_to_expiry,
            risk_free_rate=state.params.risk_free_rate,
        )
        quote = state.pricer.price_option(
            symbol=symbol,
            as_of=day,
            option_type="c",
            spot=spot.spot,
            strike=strike,
            expiration=expiration,
            sigma=spot.sigma,
            risk_free_rate=state.params.risk_free_rate,
        )
        # Sell at mid less slippage to be conservative.
        credit_per_share = max(quote.mid - state.params.slippage_per_share, 0.0)
        if credit_per_share <= 0:
            continue
        fee_open = state.params.fee_per_contract * contracts
        # Re-use the cycle id of the first lot — covered calls belong to the
        # same wheel cycle that produced the assignment.
        cycle_id = lots[0].cycle_id
        position = OptionPosition(
            cycle_id=cycle_id,
            symbol=symbol,
            leg_type="covered_call",
            contracts=contracts,
            strike=strike,
            expiration=expiration,
            entry_date=day,
            entry_premium=credit_per_share,
            fees_open=fee_open,
            cost_basis=weighted_basis,
        )
        state.portfolio.add_option(position)
        state.portfolio.credit(credit_per_share * position.shares_covered - fee_open)
        state.pending.append(
            _PendingTrade(
                cycle_id=cycle_id,
                symbol=symbol,
                leg_type=LEG_CC_OPEN,
                entry_date=day,
                exit_date=None,
                strike=strike,
                expiration=expiration,
                entry_price=credit_per_share,
                exit_price=None,
                outcome="open",
                realized_pnl=None,
                fees=fee_open,
                meta={
                    "leg": "covered_call",
                    "contracts": contracts,
                    "shares_covered": position.shares_covered,
                    "spot_at_entry": round(spot.spot, 4),
                    "sigma_used": round(spot.sigma, 6),
                    "strike": strike,
                    "underlying_cost_basis": round(weighted_basis, 4),
                    "quote_mid": round(quote.mid, 4),
                    "slippage_per_share": state.params.slippage_per_share,
                    "premium_per_share": round(credit_per_share, 4),
                    "premium_total_credit": round(credit_per_share * position.shares_covered, 2),
                    "fees_open": round(fee_open, 2),
                    "days_to_expiry_at_open": days_to_expiry,
                    "delta_target": state.params.delta_target,
                    "explanation": (
                        f"Sold {contracts} CC against {position.shares_covered} "
                        f"shares (basis ${weighted_basis:.2f}). Strike ${strike:.2f} "
                        f"chosen at delta target {state.params.delta_target:.2f}; "
                        f"premium ${credit_per_share:.4f}/sh = "
                        f"${credit_per_share * position.shares_covered:.2f} credit."
                    ),
                },
            )
        )


def _open_short_puts(
    session: Session,
    state: _SimState,
    day: date,
    spot_cache: dict[str, MarkInputs],
) -> None:
    if state.portfolio.total_open_positions() >= state.params.max_concurrent_positions:
        return
    candidates = _rank_candidates(session, state, day)
    if not candidates:
        return
    open_now = state.portfolio.open_symbols()

    for evaluation in candidates:
        if state.portfolio.total_open_positions() >= state.params.max_concurrent_positions:
            return
        symbol = evaluation.symbol
        if symbol in open_now:
            continue
        spot = spot_cache.get(symbol)
        if spot is None:
            continue
        expiration = state.pricer.select_expiration(
            symbol=symbol, as_of=day, dte_target=state.params.dte_target
        )
        days_to_expiry = (expiration - day).days
        if days_to_expiry < state.params.min_dte_for_entry:
            continue
        strike = state.pricer.select_put_strike(
            symbol=symbol,
            as_of=day,
            spot=spot.spot,
            target_delta=state.params.delta_target,
            expiration=expiration,
            sigma=spot.sigma,
            days_to_expiry=days_to_expiry,
            risk_free_rate=state.params.risk_free_rate,
        )
        contracts = state.params.contracts_per_position
        collateral = collateral_required(strike, contracts)
        if state.portfolio.free_cash < collateral:
            continue
        quote = state.pricer.price_option(
            symbol=symbol,
            as_of=day,
            option_type="p",
            spot=spot.spot,
            strike=strike,
            expiration=expiration,
            sigma=spot.sigma,
            risk_free_rate=state.params.risk_free_rate,
        )
        credit_per_share = max(quote.mid - state.params.slippage_per_share, 0.0)
        if credit_per_share <= 0:
            continue
        fee_open = state.params.fee_per_contract * contracts
        cycle_id = state.portfolio.next_cycle()
        position = OptionPosition(
            cycle_id=cycle_id,
            symbol=symbol,
            leg_type="short_put",
            contracts=contracts,
            strike=strike,
            expiration=expiration,
            entry_date=day,
            entry_premium=credit_per_share,
            fees_open=fee_open,
        )
        state.portfolio.add_option(position)
        state.portfolio.credit(credit_per_share * position.shares_covered - fee_open)
        open_now.add(symbol)
        state.pending.append(
            _PendingTrade(
                cycle_id=cycle_id,
                symbol=symbol,
                leg_type=LEG_CSP_OPEN,
                entry_date=day,
                exit_date=None,
                strike=strike,
                expiration=expiration,
                entry_price=credit_per_share,
                exit_price=None,
                outcome="open",
                realized_pnl=None,
                fees=fee_open,
                meta={
                    "leg": "short_put",
                    "contracts": contracts,
                    "shares_covered": position.shares_covered,
                    "spot_at_entry": round(spot.spot, 4),
                    "sigma_used": round(spot.sigma, 6),
                    "strike": strike,
                    "quote_mid": round(quote.mid, 4),
                    "slippage_per_share": state.params.slippage_per_share,
                    "premium_per_share": round(credit_per_share, 4),
                    "premium_total_credit": round(credit_per_share * position.shares_covered, 2),
                    "fees_open": round(fee_open, 2),
                    "collateral_locked": round(collateral, 2),
                    "days_to_expiry_at_open": days_to_expiry,
                    "delta_target": state.params.delta_target,
                    "filter_score": (
                        round(evaluation.score, 6) if evaluation.score is not None else None
                    ),
                    "explanation": (
                        f"Sold {contracts} CSP at strike ${strike:.2f} "
                        f"(delta target {state.params.delta_target:.2f}, "
                        f"sigma {spot.sigma:.4f}). Premium ${credit_per_share:.4f}/sh "
                        f"= ${credit_per_share * position.shares_covered:.2f} credit; "
                        f"collateral ${collateral:.2f} locked."
                    ),
                },
            )
        )


def _rank_candidates(session: Session, state: _SimState, day: date) -> list[SymbolEvaluation]:
    """Return the day's passing-symbol evaluations, highest score first."""
    passers: list[SymbolEvaluation] = []
    for symbol in state.universe:
        ctx = build_context(session, symbol, day, include_options=False)
        if ctx is None:
            continue
        try:
            evaluation = evaluate_symbol(ctx, state.config)
        except Exception as exc:
            log.warning(
                "backtest.symbol.error",
                run_id=state.run_id,
                date=day.isoformat(),
                symbol=symbol,
                error=f"{type(exc).__name__}: {exc}",
            )
            continue
        if not evaluation.passed:
            continue
        passers.append(evaluation)
    passers.sort(key=lambda e: (e.score is None, -(e.score or 0.0), e.symbol))
    return passers


def _load_spot_cache(session: Session, universe: Sequence[str], day: date) -> dict[str, MarkInputs]:
    """Latest close <= ``day`` and a vol estimate per symbol."""
    out: dict[str, MarkInputs] = {}
    for symbol in universe:
        bar = session.execute(
            select(BarDaily.close)
            .where(BarDaily.symbol == symbol, BarDaily.date <= day)
            .order_by(BarDaily.date.desc())
            .limit(1)
        ).scalar_one_or_none()
        if bar is None:
            continue
        ind = session.execute(
            select(IndicatorDaily.iv_atm, IndicatorDaily.hv_20)
            .where(IndicatorDaily.symbol == symbol, IndicatorDaily.date <= day)
            .order_by(IndicatorDaily.date.desc())
            .limit(1)
        ).first()
        iv_atm = float(ind[0]) if ind and ind[0] is not None else None
        hv_20 = float(ind[1]) if ind and ind[1] is not None else None

        realized: float | None = None
        if iv_atm is None and hv_20 is None:
            closes = (
                session.execute(
                    select(BarDaily.close)
                    .where(BarDaily.symbol == symbol, BarDaily.date <= day)
                    .order_by(BarDaily.date.desc())
                    .limit(60)
                )
                .scalars()
                .all()
            )
            realized = realized_vol_from_closes(list(reversed([float(c) for c in closes])))

        sigma = estimate_sigma(iv_atm=iv_atm, hv_20=hv_20, realized_fallback=realized)
        out[symbol] = MarkInputs(spot=float(bar), sigma=sigma)
    return out


def _take_lots(lots: list[ShareLot], symbol: str, shares_needed: int) -> list[ShareLot]:
    """Pick whole share lots (FIFO) totalling ``shares_needed``.

    The simulator always opens covered calls at one-contract-per-100-shares
    granularity, so partial-lot accounting isn't needed: each ``ShareLot`` is
    sized in 100-share chunks at assignment time. We still iterate FIFO so
    multiple cycles on the same symbol are unwound oldest-first.
    """
    out: list[ShareLot] = []
    remaining = shares_needed
    for lot in lots:
        if lot.symbol != symbol:
            continue
        if remaining <= 0:
            break
        if lot.shares <= remaining:
            out.append(lot)
            remaining -= lot.shares
        else:
            # Splitting a lot is unusual; we treat it as taking the whole
            # lot's basis but only the requested share count. The leftover
            # remains in the portfolio.
            out.append(
                ShareLot(
                    cycle_id=lot.cycle_id,
                    symbol=lot.symbol,
                    shares=remaining,
                    cost_basis=lot.cost_basis,
                    acquired_date=lot.acquired_date,
                )
            )
            lot.shares -= remaining
            remaining = 0
    return out


def _load_universe(session: Session, symbols: Sequence[str] | None) -> list[str]:
    stmt = select(Ticker.symbol).where(Ticker.is_active.is_(True), Ticker.is_hidden.is_(False))
    if symbols is not None:
        stmt = stmt.where(Ticker.symbol.in_({s.upper() for s in symbols}))
    return list(session.execute(stmt.order_by(Ticker.symbol)).scalars().all())


def _trading_days(calendar_name: str, start: date, end: date) -> list[date]:
    cal = mcal.get_calendar(calendar_name)
    schedule = cal.schedule(start_date=start, end_date=end)
    return [pd.Timestamp(ts).date() for ts in schedule.index]


def _open_leg_label(leg_type: str) -> str:
    return LEG_CSP_OPEN if leg_type == "short_put" else LEG_CC_OPEN


def _flush_trades(session: Session, run_id: int, pending: list[_PendingTrade]) -> None:
    if not pending:
        return
    rows: list[dict[str, Any]] = [
        {
            "run_id": run_id,
            "cycle_id": t.cycle_id,
            "symbol": t.symbol,
            "leg_type": t.leg_type,
            "entry_date": t.entry_date,
            "exit_date": t.exit_date,
            "strike": t.strike,
            "expiration": t.expiration,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "outcome": t.outcome,
            "realized_pnl": t.realized_pnl,
            "fees": t.fees,
            "meta": t.meta,
        }
        for t in pending
    ]
    session.execute(insert(BacktestTrade), rows)
