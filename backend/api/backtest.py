"""Backtest resource — run launcher (filter + strategy), run history, per-run trades + equity."""

from __future__ import annotations

import csv
import io
import json
import statistics
from collections.abc import Sequence
from datetime import date as DateType
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from backtest.filter_backtest import (
    DEFAULT_STARTING_CAPITAL as FILTER_DEFAULT_CAPITAL,
)
from backtest.filter_backtest import (
    run_filter_backtest,
)
from backtest.simulator import StrategyParams, run_strategy_backtest
from core.logging import get_logger
from db import get_session
from db.models.backtest import (
    MODE_FILTER,
    MODE_STRATEGY,
    STATUS_FAILED,
    STATUS_RUNNING,
    BacktestEquity,
    BacktestRun,
    BacktestTrade,
)
from db.models.market import MacroDaily
from db.models.screener import FilterConfig

log = get_logger(__name__)

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

# Trade leg types that mark the *end* of a wheel cycle (share state → cash).
# csp_close is conditional — only closes a cycle when no covered-call legs
# exist for the same cycle (i.e., put was closed before assignment).
_CYCLE_TERMINATING_LEGS = {"csp_expired", "cc_assigned"}
_CC_LEGS = {"cc_open", "cc_close", "cc_assigned", "cc_expired"}


class StrategyParamsIn(BaseModel):
    """Tunables exposed on the strategy launcher; defaults match `StrategyParams`."""

    starting_capital: float = Field(default=10_000.0, gt=0)
    max_concurrent_positions: int = Field(default=5, ge=1, le=50)
    dte_target: int = Field(default=30, ge=1, le=120)
    delta_target: float = Field(default=0.30, gt=0, lt=1)
    profit_take_pct: float = Field(default=0.50, gt=0, le=1)
    manage_dte: int = Field(default=21, ge=0, le=60)
    fee_per_contract: float = Field(default=0.65, ge=0)
    slippage_per_share: float = Field(default=0.02, ge=0)
    hold_losers_to_expiry: bool = Field(default=False)


class BacktestRunIn(BaseModel):
    mode: Literal["filter", "strategy"] = "filter"
    config_id: int
    start_date: DateType
    end_date: DateType
    forward_days: int = Field(default=30, ge=1, le=252)
    symbols: list[str] | None = None
    strategy_params: StrategyParamsIn | None = None

    @model_validator(mode="after")
    def _check_window(self) -> BacktestRunIn:
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self


class BacktestTradeOut(BaseModel):
    id: int
    symbol: str
    cycle_id: int | None
    leg_type: str
    entry_date: DateType
    exit_date: DateType | None
    strike: float | None
    expiration: DateType | None
    entry_price: float
    exit_price: float | None
    outcome: str | None
    realized_pnl: float | None
    realized_pnl_pct: float | None
    fees: float
    meta: dict[str, Any] | None


class BacktestRunOut(BaseModel):
    id: int
    config_id: int | None
    config_name: str | None
    mode: str
    status: str
    error_message: str | None
    start_date: DateType
    end_date: DateType
    starting_capital: float
    params_json: dict[str, Any] | None
    created_at: datetime
    trade_count: int
    # Filter-mode metrics (None for strategy runs)
    win_rate: float | None
    mean_return_pct: float | None
    median_return_pct: float | None
    # Strategy-mode metrics (None for filter runs)
    final_equity: float | None
    total_return_pct: float | None
    cycles_completed: int | None


class BacktestEquityPoint(BaseModel):
    date: DateType
    equity: float
    cash: float
    collateral_locked: float
    unrealized_pnl: float
    spy_benchmark: float | None = None


@router.get("/runs", response_model=list[BacktestRunOut])
def list_runs() -> list[BacktestRunOut]:
    with get_session() as session:
        runs = (
            session.execute(select(BacktestRun).order_by(desc(BacktestRun.created_at)))
            .scalars()
            .all()
        )
        return [_build_run_out(session, run) for run in runs]


@router.post("/runs", response_model=BacktestRunOut, status_code=202)
def create_run(payload: BacktestRunIn, background_tasks: BackgroundTasks) -> BacktestRunOut:
    """Pre-create the run row in `running` state and dispatch the work to a background task.

    Returns 202 + the run snapshot immediately so the client can start polling
    `GET /runs/{id}` for status flips.
    """
    with get_session() as session:
        config = session.get(FilterConfig, payload.config_id)
        if config is None:
            raise HTTPException(status_code=400, detail=f"config not found: {payload.config_id}")

        if payload.mode == MODE_STRATEGY:
            sp = payload.strategy_params or StrategyParamsIn()
            starting_capital = sp.starting_capital
            params_json: dict[str, Any] = {
                "calendar": "NYSE",
                **sp.model_dump(),
            }
        else:
            starting_capital = FILTER_DEFAULT_CAPITAL
            params_json = {
                "forward_days": payload.forward_days,
                "calendar": "NYSE",
            }

        run = BacktestRun(
            config_id=payload.config_id,
            mode=payload.mode,
            status=STATUS_RUNNING,
            start_date=payload.start_date,
            end_date=payload.end_date,
            starting_capital=starting_capital,
            params_json=params_json,
        )
        session.add(run)
        session.flush()
        run_id = run.id
        snapshot = _build_run_out(session, run)
        log.info(
            "backtest.api.run.created",
            run_id=run_id,
            mode=payload.mode,
            config_id=payload.config_id,
        )

    if payload.mode == MODE_STRATEGY:
        sp = payload.strategy_params or StrategyParamsIn()
        background_tasks.add_task(
            _run_strategy_in_background,
            run_id=run_id,
            config_id=payload.config_id,
            start_date=payload.start_date,
            end_date=payload.end_date,
            symbols=payload.symbols,
            params=StrategyParams(
                starting_capital=sp.starting_capital,
                max_concurrent_positions=sp.max_concurrent_positions,
                dte_target=sp.dte_target,
                delta_target=sp.delta_target,
                profit_take_pct=sp.profit_take_pct,
                manage_dte=sp.manage_dte,
                fee_per_contract=sp.fee_per_contract,
                slippage_per_share=sp.slippage_per_share,
                hold_losers_to_expiry=sp.hold_losers_to_expiry,
            ),
        )
    else:
        background_tasks.add_task(
            _run_filter_in_background,
            run_id=run_id,
            config_id=payload.config_id,
            start_date=payload.start_date,
            end_date=payload.end_date,
            forward_days=payload.forward_days,
            symbols=payload.symbols,
        )

    return snapshot


@router.get("/runs/{run_id}", response_model=BacktestRunOut)
def get_run(run_id: int) -> BacktestRunOut:
    with get_session() as session:
        run = session.get(BacktestRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        return _build_run_out(session, run)


@router.get("/runs/{run_id}/trades", response_model=list[BacktestTradeOut])
def list_trades(run_id: int) -> list[BacktestTradeOut]:
    with get_session() as session:
        run = session.get(BacktestRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        trades = (
            session.execute(
                select(BacktestTrade)
                .where(BacktestTrade.run_id == run_id)
                .order_by(BacktestTrade.entry_date, BacktestTrade.symbol)
            )
            .scalars()
            .all()
        )
        return [_trade_out(t, run.mode) for t in trades]


@router.get("/runs/{run_id}/trades.csv")
def export_trades_csv(run_id: int) -> Response:
    """Export this run's trades as CSV with run-level metadata in a header preamble.

    Designed for offline review (paste-into-Claude / spreadsheet sanity checks):
    the run config, params, and summary metrics travel with the trade rows so
    the CSV is self-contained. Meta keys are flattened (``pnl.<sub>`` for the
    P&L breakdown, ``meta.<key>`` for per-leg diagnostics) and ``lots`` is
    JSON-encoded since it's a variable-length list of dicts.
    """
    with get_session() as session:
        run = session.get(BacktestRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        run_out = _build_run_out(session, run)
        trades = (
            session.execute(
                select(BacktestTrade)
                .where(BacktestTrade.run_id == run_id)
                .order_by(BacktestTrade.entry_date, BacktestTrade.symbol, BacktestTrade.id)
            )
            .scalars()
            .all()
        )

    body = _render_trades_csv(run_out, trades)
    filename = f"backtest_run_{run_id}_trades.csv"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/runs/{run_id}/equity", response_model=list[BacktestEquityPoint])
def list_equity(run_id: int) -> list[BacktestEquityPoint]:
    with get_session() as session:
        run = session.get(BacktestRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        rows = (
            session.execute(
                select(BacktestEquity)
                .where(BacktestEquity.run_id == run_id)
                .order_by(BacktestEquity.date)
            )
            .scalars()
            .all()
        )
        if not rows:
            return []

        # Build a {date: spy_close} map from macro_daily for the run's date range.
        macro_rows = session.execute(
            select(MacroDaily.date, MacroDaily.spy_close)
            .where(
                MacroDaily.date >= rows[0].date,
                MacroDaily.date <= rows[-1].date,
                MacroDaily.spy_close.is_not(None),
            )
            .order_by(MacroDaily.date)
        ).all()
        spy_by_date: dict[DateType, float] = {r.date: float(r.spy_close) for r in macro_rows}  # type: ignore[arg-type]

        # Normalize SPY to the strategy's starting capital using the first
        # available SPY close on or after the run's first equity date.
        spy_start: float | None = None
        for eq_row in rows:
            if eq_row.date in spy_by_date:
                spy_start = spy_by_date[eq_row.date]
                break

        capital = float(run.starting_capital)

        def _spy_benchmark(d: DateType) -> float | None:
            if spy_start is None or d not in spy_by_date:
                return None
            return capital * spy_by_date[d] / spy_start

        return [
            BacktestEquityPoint(
                date=row.date,
                equity=row.equity,
                cash=row.cash,
                collateral_locked=row.collateral_locked,
                unrealized_pnl=row.unrealized_pnl,
                spy_benchmark=_spy_benchmark(row.date),
            )
            for row in rows
        ]


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(run_id: int) -> Response:
    with get_session() as session:
        run = session.get(BacktestRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        session.delete(run)
        log.info("backtest.api.run.deleted", run_id=run_id)
    return Response(status_code=204)


def _run_filter_in_background(
    *,
    run_id: int,
    config_id: int,
    start_date: DateType,
    end_date: DateType,
    forward_days: int,
    symbols: list[str] | None,
) -> None:
    with get_session() as session:
        try:
            run_filter_backtest(
                session,
                config_id=config_id,
                start_date=start_date,
                end_date=end_date,
                forward_days=forward_days,
                symbols=symbols,
                existing_run_id=run_id,
            )
        except Exception:
            # `_mark_failed` inside the runner has already flipped the row to
            # status='failed' with the error message — swallow here so the
            # background worker doesn't crash the process.
            log.exception("backtest.background.filter.failed", run_id=run_id)


def _run_strategy_in_background(
    *,
    run_id: int,
    config_id: int,
    start_date: DateType,
    end_date: DateType,
    symbols: list[str] | None,
    params: StrategyParams,
) -> None:
    with get_session() as session:
        try:
            run_strategy_backtest(
                session,
                config_id=config_id,
                start_date=start_date,
                end_date=end_date,
                params=params,
                symbols=symbols,
                existing_run_id=run_id,
            )
        except Exception:
            log.exception("backtest.background.strategy.failed", run_id=run_id)


def _build_run_out(session: Session, run: BacktestRun) -> BacktestRunOut:
    config_name: str | None = None
    if run.config_id is not None:
        config = session.get(FilterConfig, run.config_id)
        config_name = config.name if config else None

    trades = (
        session.execute(select(BacktestTrade).where(BacktestTrade.run_id == run.id)).scalars().all()
    )

    win_rate = mean_return_pct = median_return_pct = None
    final_equity = total_return_pct = None
    cycles_completed: int | None = None

    if run.mode == MODE_FILTER:
        returns = [t.realized_pnl / 100.0 for t in trades if t.realized_pnl is not None]
        if returns:
            wins = [r for r in returns if r > 0]
            win_rate = len(wins) / len(returns)
            mean_return_pct = statistics.mean(returns) * 100
            median_return_pct = statistics.median(returns) * 100
    elif run.mode == MODE_STRATEGY:
        last_equity = session.execute(
            select(BacktestEquity.equity)
            .where(BacktestEquity.run_id == run.id)
            .order_by(desc(BacktestEquity.date))
            .limit(1)
        ).scalar_one_or_none()
        if last_equity is not None:
            final_equity = float(last_equity)
            if run.starting_capital > 0:
                total_return_pct = (
                    (final_equity - run.starting_capital) / run.starting_capital * 100.0
                )
        cycles_completed = _count_completed_cycles(trades)

    return BacktestRunOut(
        id=run.id,
        config_id=run.config_id,
        config_name=config_name,
        mode=run.mode,
        status=run.status,
        error_message=run.error_message,
        start_date=run.start_date,
        end_date=run.end_date,
        starting_capital=run.starting_capital,
        params_json=run.params_json,
        created_at=run.created_at,
        trade_count=len(trades),
        win_rate=win_rate,
        mean_return_pct=mean_return_pct,
        median_return_pct=median_return_pct,
        final_equity=final_equity,
        total_return_pct=total_return_pct,
        cycles_completed=cycles_completed,
    )


def _count_completed_cycles(trades: Sequence[BacktestTrade]) -> int:
    """Mirror the simulator's cycles-completed accounting from persisted trades.

    A cycle returns to all-cash (and is therefore "complete") when one of:
      - csp_expired (put expired OTM, no shares acquired)
      - cc_assigned (shares called away)
      - csp_close where no covered-call leg exists in the same cycle
        (put profit-taken before assignment).
    """
    closed: set[int] = set()
    csp_close_cycles: set[int] = set()
    cc_cycles: set[int] = set()
    for t in trades:
        if t.cycle_id is None:
            continue
        if t.leg_type in _CYCLE_TERMINATING_LEGS:
            closed.add(t.cycle_id)
        elif t.leg_type == "csp_close":
            csp_close_cycles.add(t.cycle_id)
        if t.leg_type in _CC_LEGS:
            cc_cycles.add(t.cycle_id)
    closed |= csp_close_cycles - cc_cycles
    return len(closed)


def _trade_out(t: BacktestTrade, mode: str) -> BacktestTradeOut:
    realized_pnl: float | None = t.realized_pnl
    realized_pnl_pct: float | None = None
    if mode == MODE_FILTER and t.realized_pnl is not None:
        # Filter trades store percentage returns (pct * 100) in `realized_pnl`.
        # Surface that as a percentage and leave dollar P&L unset.
        realized_pnl_pct = t.realized_pnl
        realized_pnl = None
    return BacktestTradeOut(
        id=t.id,
        symbol=t.symbol,
        cycle_id=t.cycle_id,
        leg_type=t.leg_type,
        entry_date=t.entry_date,
        exit_date=t.exit_date,
        strike=t.strike,
        expiration=t.expiration,
        entry_price=t.entry_price,
        exit_price=t.exit_price,
        outcome=t.outcome,
        realized_pnl=realized_pnl,
        realized_pnl_pct=realized_pnl_pct,
        fees=t.fees,
        meta=t.meta,
    )


# Standard trade columns emitted in every CSV, in this order. Meta-derived
# columns are appended after these based on what's actually present.
_CSV_TRADE_COLUMNS: tuple[str, ...] = (
    "id",
    "run_id",
    "cycle_id",
    "symbol",
    "leg_type",
    "entry_date",
    "exit_date",
    "expiration",
    "strike",
    "entry_price",
    "exit_price",
    "outcome",
    "realized_pnl",
    "realized_pnl_pct",
    "fees",
)


def _flatten_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Flatten a trade's ``meta`` dict into CSV-friendly scalar columns.

    - ``pnl_breakdown.<k>`` → ``pnl.<k>`` (one column per sub-key).
    - ``lots`` → ``lots_count`` and ``lots_total_shares`` summary columns,
      plus a JSON-encoded ``lots_json`` for full fidelity.
    - Scalar keys → ``meta.<key>``.
    - Any remaining nested dict/list → JSON-encoded under ``meta.<key>``.
    """
    if not meta:
        return {}
    out: dict[str, Any] = {}
    for key, value in meta.items():
        if key == "pnl_breakdown" and isinstance(value, dict):
            for sub_key, sub_value in value.items():
                out[f"pnl.{sub_key}"] = sub_value
        elif key == "lots" and isinstance(value, list):
            out["lots_count"] = len(value)
            total_shares = 0
            for lot in value:
                if isinstance(lot, dict):
                    shares = lot.get("shares")
                    if isinstance(shares, (int, float)):
                        total_shares += int(shares)
            out["lots_total_shares"] = total_shares
            out["lots_json"] = json.dumps(value, default=str, separators=(",", ":"))
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[f"meta.{key}"] = value
        else:
            out[f"meta.{key}"] = json.dumps(value, default=str, separators=(",", ":"))
    return out


def _render_trades_csv(run: BacktestRunOut, trades: Sequence[BacktestTrade]) -> str:
    """Serialise the run's trades as CSV with a metadata preamble.

    The preamble is a block of ``# key=value`` lines (CSV readers ignore
    them when stripped, and humans / LLMs can scan them at a glance) followed
    by a blank line and the actual table.
    """
    flattened: list[dict[str, Any]] = []
    extra_keys: list[str] = []
    seen: set[str] = set()
    for trade in trades:
        flat = _flatten_meta(trade.meta)
        flattened.append(flat)
        for key in flat:
            if key not in seen:
                seen.add(key)
                extra_keys.append(key)

    columns = list(_CSV_TRADE_COLUMNS) + extra_keys

    buf = io.StringIO()
    for line in _csv_preamble_lines(run, trade_count=len(trades)):
        buf.write(f"# {line}\n")
    buf.write("#\n")

    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for trade, flat in zip(trades, flattened, strict=True):
        realized_pnl: float | None = trade.realized_pnl
        realized_pnl_pct: float | None = None
        # Filter mode stores % return in realized_pnl; surface both columns
        # explicitly so the CSV is unambiguous regardless of mode.
        if run.mode == MODE_FILTER and trade.realized_pnl is not None:
            realized_pnl_pct = trade.realized_pnl
            realized_pnl = None

        row: dict[str, Any] = {
            "id": trade.id,
            "run_id": run.id,
            "cycle_id": trade.cycle_id,
            "symbol": trade.symbol,
            "leg_type": trade.leg_type,
            "entry_date": trade.entry_date.isoformat() if trade.entry_date else "",
            "exit_date": trade.exit_date.isoformat() if trade.exit_date else "",
            "expiration": trade.expiration.isoformat() if trade.expiration else "",
            "strike": trade.strike,
            "entry_price": trade.entry_price,
            "exit_price": trade.exit_price,
            "outcome": trade.outcome,
            "realized_pnl": realized_pnl,
            "realized_pnl_pct": realized_pnl_pct,
            "fees": trade.fees,
        }
        for key, value in flat.items():
            row[key] = value
        writer.writerow({k: ("" if v is None else v) for k, v in row.items()})

    return buf.getvalue()


def _csv_preamble_lines(run: BacktestRunOut, *, trade_count: int) -> list[str]:
    lines = [
        "Penny Pincher Pro — backtest trade export",
        f"run_id={run.id}",
        f"mode={run.mode}",
        f"status={run.status}",
        f"config_id={run.config_id if run.config_id is not None else ''}",
        f"config_name={run.config_name or ''}",
        f"start_date={run.start_date.isoformat()}",
        f"end_date={run.end_date.isoformat()}",
        f"starting_capital={run.starting_capital}",
        f"created_at={run.created_at.isoformat()}",
        f"trade_count={trade_count}",
    ]
    if run.error_message:
        # Keep the error on a single line; newlines would break the preamble.
        lines.append(f"error_message={run.error_message.replace(chr(10), ' ')}")
    if run.mode == MODE_STRATEGY:
        lines.append(f"final_equity={run.final_equity if run.final_equity is not None else ''}")
        lines.append(
            f"total_return_pct={run.total_return_pct if run.total_return_pct is not None else ''}"
        )
        lines.append(
            f"cycles_completed={run.cycles_completed if run.cycles_completed is not None else ''}"
        )
    else:
        lines.append(f"win_rate={run.win_rate if run.win_rate is not None else ''}")
        lines.append(
            f"mean_return_pct={run.mean_return_pct if run.mean_return_pct is not None else ''}"
        )
        median = run.median_return_pct if run.median_return_pct is not None else ""
        lines.append(f"median_return_pct={median}")
    if run.params_json:
        for key in sorted(run.params_json):
            lines.append(f"param.{key}={run.params_json[key]}")
    return lines


__all__ = ["MODE_FILTER", "MODE_STRATEGY", "STATUS_FAILED", "router"]
