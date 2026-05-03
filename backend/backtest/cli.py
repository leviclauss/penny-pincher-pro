"""CLI entry point: ``python -m backtest.cli ...``."""

from __future__ import annotations

from datetime import date, datetime

import click
from sqlalchemy import select

from core.config import get_settings
from core.logging import configure_logging
from db.models.backtest import BacktestTrade
from db.session import get_session

from .filter_backtest import DEFAULT_CALENDAR, run_filter_backtest


def _parse_date(_ctx: click.Context, _param: click.Parameter, value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


@click.command(context_settings={"show_default": True})
@click.option("--config-id", type=int, required=True, help="filter_configs.id to evaluate.")
@click.option("--start", "start", required=True, callback=_parse_date, help="YYYY-MM-DD.")
@click.option("--end", "end", required=True, callback=_parse_date, help="YYYY-MM-DD.")
@click.option(
    "--forward-days",
    type=int,
    default=30,
    help="Trading days from entry to the exit close.",
)
@click.option(
    "--symbols",
    "symbols",
    default=None,
    help="Comma-separated symbols; default = every active ticker.",
)
@click.option("--calendar", default=DEFAULT_CALENDAR, help="pandas_market_calendars name.")
def cli(
    config_id: int,
    start: date,
    end: date,
    forward_days: int,
    symbols: str | None,
    calendar: str,
) -> None:
    """Replay one screener config across history and record forward returns."""
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)

    symbol_list = [s.strip().upper() for s in symbols.split(",")] if symbols else None

    with get_session() as session:
        run_id = run_filter_backtest(
            session,
            config_id=config_id,
            start_date=start,
            end_date=end,
            forward_days=forward_days,
            symbols=symbol_list,
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


if __name__ == "__main__":
    cli()
