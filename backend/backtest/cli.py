"""CLI entry point: ``python -m backtest.cli ...``.

Two modes:

- ``--mode filter`` (default; back-compat) — replays a screener config
  day-by-day and writes one ``filter_pass`` row per (symbol, day) with
  the realized forward-N-day return.
- ``--mode strategy`` — full wheel simulator with synthetic option pricing,
  capital management, and equity-curve persistence. See ``simulator.py``.
"""

from __future__ import annotations

from datetime import date, datetime

import click
from sqlalchemy import select

from core.config import get_settings
from core.logging import configure_logging
from db.models.backtest import BacktestTrade
from db.session import get_session

from .filter_backtest import DEFAULT_CALENDAR, run_filter_backtest
from .pricing import RealChainPricer
from .simulator import (
    DEFAULT_DELTA_TARGET,
    DEFAULT_DTE_TARGET,
    DEFAULT_FEE_PER_CONTRACT,
    DEFAULT_MANAGE_DTE,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_PROFIT_TAKE_PCT,
    DEFAULT_SLIPPAGE_PER_SHARE,
    StrategyParams,
    run_strategy_backtest,
)


def _parse_date(_ctx: click.Context, _param: click.Parameter, value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


@click.command(context_settings={"show_default": True})
@click.option(
    "--mode",
    type=click.Choice(["filter", "strategy"]),
    default="filter",
    help="``filter`` = forward-return only; ``strategy`` = full wheel simulator.",
)
@click.option("--config-id", type=int, required=True, help="filter_configs.id to evaluate.")
@click.option("--start", "start", required=True, callback=_parse_date, help="YYYY-MM-DD.")
@click.option("--end", "end", required=True, callback=_parse_date, help="YYYY-MM-DD.")
@click.option(
    "--forward-days",
    type=int,
    default=30,
    help="(filter mode) Trading days from entry to the exit close.",
)
@click.option(
    "--symbols",
    "symbols",
    default=None,
    help="Comma-separated symbols; default = every active ticker.",
)
@click.option("--calendar", default=DEFAULT_CALENDAR, help="pandas_market_calendars name.")
@click.option(
    "--starting-capital",
    type=float,
    default=10_000.0,
    help="(strategy mode) Cash the simulator starts with.",
)
@click.option(
    "--max-concurrent-positions",
    type=int,
    default=DEFAULT_MAX_CONCURRENT,
    help="(strategy mode) Cap on simultaneous open wheel positions.",
)
@click.option(
    "--dte-target",
    type=int,
    default=DEFAULT_DTE_TARGET,
    help="(strategy mode) Days-to-expiry target for new short puts/calls.",
)
@click.option(
    "--delta-target",
    type=float,
    default=DEFAULT_DELTA_TARGET,
    help="(strategy mode) Magnitude of target delta (e.g. 0.30).",
)
@click.option(
    "--profit-take-pct",
    type=float,
    default=DEFAULT_PROFIT_TAKE_PCT,
    help="(strategy mode) Close at this fraction of max profit.",
)
@click.option(
    "--manage-dte",
    type=int,
    default=DEFAULT_MANAGE_DTE,
    help="(strategy mode) Close/roll when DTE drops to this threshold.",
)
@click.option(
    "--fee-per-contract",
    type=float,
    default=DEFAULT_FEE_PER_CONTRACT,
    help="(strategy mode) Commission per option contract per side.",
)
@click.option(
    "--slippage-per-share",
    type=float,
    default=DEFAULT_SLIPPAGE_PER_SHARE,
    help="(strategy mode) Per-share slippage cost on each option fill.",
)
@click.option(
    "--use-real-chain",
    is_flag=True,
    default=False,
    help=(
        "(strategy mode) Use options_historical for pricing + strike selection "
        "instead of synthetic Black-Scholes against a sigma estimate. Requires "
        "an options-history backfill (see ingestion/options_history.py)."
    ),
)
@click.option(
    "--hold-losers-to-expiry",
    is_flag=True,
    default=False,
    help=(
        "(strategy mode) True-wheel discipline: skip the manage-DTE close "
        "when buying back the leg would realize a loss. ITM puts ride to "
        "assignment (shares + covered call next), ITM calls deliver shares "
        "at the cost-basis-floored strike. Profit-take rule is unchanged."
    ),
)
def cli(
    mode: str,
    config_id: int,
    start: date,
    end: date,
    forward_days: int,
    symbols: str | None,
    calendar: str,
    starting_capital: float,
    max_concurrent_positions: int,
    dte_target: int,
    delta_target: float,
    profit_take_pct: float,
    manage_dte: int,
    fee_per_contract: float,
    slippage_per_share: float,
    use_real_chain: bool,
    hold_losers_to_expiry: bool,
) -> None:
    """Replay one screener config across history."""
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)

    symbol_list = [s.strip().upper() for s in symbols.split(",")] if symbols else None

    if mode == "filter":
        _run_filter_mode(
            config_id=config_id,
            start=start,
            end=end,
            forward_days=forward_days,
            symbols=symbol_list,
            calendar=calendar,
        )
    else:
        params = StrategyParams(
            starting_capital=starting_capital,
            max_concurrent_positions=max_concurrent_positions,
            dte_target=dte_target,
            delta_target=delta_target,
            profit_take_pct=profit_take_pct,
            manage_dte=manage_dte,
            fee_per_contract=fee_per_contract,
            slippage_per_share=slippage_per_share,
            risk_free_rate=settings.risk_free_rate,
            hold_losers_to_expiry=hold_losers_to_expiry,
        )
        _run_strategy_mode(
            config_id=config_id,
            start=start,
            end=end,
            symbols=symbol_list,
            calendar=calendar,
            params=params,
            use_real_chain=use_real_chain,
        )


def _run_filter_mode(
    *,
    config_id: int,
    start: date,
    end: date,
    forward_days: int,
    symbols: list[str] | None,
    calendar: str,
) -> None:
    with get_session() as session:
        run_id = run_filter_backtest(
            session,
            config_id=config_id,
            start_date=start,
            end_date=end,
            forward_days=forward_days,
            symbols=symbols,
            calendar_name=calendar,
        )
        # Re-aggregate from the persisted rows so the printed numbers match
        # exactly what got committed.
        rows = (
            session.execute(
                select(BacktestTrade.realized_pnl).where(BacktestTrade.run_id == run_id)
            )
            .scalars()
            .all()
        )

    returns = [float(r) / 100.0 for r in rows if r is not None]
    candidates = len(returns)
    if candidates == 0:
        click.echo(f"run_id={run_id} candidates=0")
        return

    mean = sum(returns) / candidates
    ordered = sorted(returns)
    mid = candidates // 2
    median = ordered[mid] if candidates % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    win_rate = sum(1 for r in returns if r > 0) / candidates

    click.echo(
        f"run_id={run_id} "
        f"candidates={candidates} "
        f"mean_return={mean:.4f} "
        f"median_return={median:.4f} "
        f"win_rate={win_rate:.2%}"
    )


def _run_strategy_mode(
    *,
    config_id: int,
    start: date,
    end: date,
    symbols: list[str] | None,
    calendar: str,
    params: StrategyParams,
    use_real_chain: bool,
) -> None:
    with get_session() as session:
        pricer = RealChainPricer(session) if use_real_chain else None
        summary = run_strategy_backtest(
            session,
            config_id=config_id,
            start_date=start,
            end_date=end,
            params=params,
            symbols=symbols,
            calendar_name=calendar,
            pricer=pricer,
        )
    click.echo(
        f"run_id={summary.run_id} "
        f"days={summary.days} "
        f"trades={summary.trades} "
        f"final_equity={summary.final_equity:.2f} "
        f"return_pct={summary.total_return_pct:.2f} "
        f"cycles_completed={summary.cycles_completed}"
    )


if __name__ == "__main__":
    cli()
