"""CLI entry point: python -m backtest.forward_returns"""

from __future__ import annotations

import click

from backtest.forward_returns import evaluate_forward_returns
from core.config import get_settings
from core.logging import configure_logging
from db import get_session


@click.command()
@click.option("--config-id", type=int, required=True, help="Filter config ID to evaluate.")
@click.option("--start", type=click.DateTime(formats=["%Y-%m-%d"]), required=True)
@click.option("--end", type=click.DateTime(formats=["%Y-%m-%d"]), required=True)
def cli(config_id: int, start: click.DateTime, end: click.DateTime) -> None:
    """Evaluate forward returns for historical screener picks."""
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)

    with get_session() as session:
        summary = evaluate_forward_returns(
            session,
            config_id=config_id,
            start_date=start.date(),  # type: ignore[union-attr]
            end_date=end.date(),  # type: ignore[union-attr]
        )

    click.echo(f"\nForward Returns: {summary.config_name} (config {summary.config_id})")
    click.echo(f"Period: {summary.start_date} → {summary.end_date}")
    click.echo(f"Total picks: {summary.total_picks}, with returns: {summary.picks_with_returns}")
    click.echo()
    click.echo(f"{'Period':<10} {'Hit Rate':>10} {'Mean':>10} {'Median':>10}")
    click.echo("-" * 42)
    for period, hr, mn, md in [
        ("5d", summary.hit_rate_5d, summary.mean_return_5d, summary.median_return_5d),
        ("10d", summary.hit_rate_10d, summary.mean_return_10d, summary.median_return_10d),
        ("21d", summary.hit_rate_21d, summary.mean_return_21d, summary.median_return_21d),
    ]:
        hr_str = f"{hr:.1%}" if hr is not None else "—"
        mn_str = f"{mn:.2%}" if mn is not None else "—"
        md_str = f"{md:.2%}" if md is not None else "—"
        click.echo(f"{period:<10} {hr_str:>10} {mn_str:>10} {md_str:>10}")


if __name__ == "__main__":
    cli()
