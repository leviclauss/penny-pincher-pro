"""Intraday alert pulse — phase 3 of the alert engine.

One scheduled job, two trigger families:

- ``setup_triggered``: a watchlist symbol newly passes a screener config
  during RTH that wasn't in this morning's digest.
- ``iv_spike``: ATM IV jumped ≥ ``intraday_iv_spike_pct`` vs the most recent
  ``indicators_daily.iv_atm`` (yesterday's close IV in practice).

Disabled by default. Enable per deployment via ``SCHEDULER_INTRADAY_ENABLED``
once Telegram is configured. Each tick:

1. Holiday + RTH gate (uses ``pandas_market_calendars``).
2. Pulls latest NBBO quotes for the active watchlist via the injected
   ``quote_source``. Symbols whose freshest quote is older than
   ``INTRADAY_QUOTE_MAX_AGE_S`` are dropped — a uniformly stale fan-in
   short-circuits the whole tick with ``skipped="stale_quotes"``.
3. **Setup pass**: synthesizes an intraday ``FilterContext`` per symbol
   (today's bar overridden with the live mid, RSI(14) recomputed; EMAs and
   IV-derived indicators kept frozen because they barely move intraday),
   evaluates every active screener config, and fires the best-scoring hit
   per symbol — suppressed if already in the morning digest or dispatched
   earlier today.
4. **IV-spike pass** (off by default, throttled per symbol via
   ``INTRADAY_IV_SPIKE_INTERVAL_MINUTES``): pulls the current chain,
   computes ATM IV, compares to the stored baseline, fires on threshold
   breach. Same per-(symbol, day) dedup as setup.

The trigger payloads always carry ``as_of`` (ISO date) and ``symbol`` so
``alerts.triggers._dedup.already_dispatched_for_symbol_on`` finds them.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
import pandas_market_calendars as mcal
from sqlalchemy import select
from sqlalchemy.orm import Session
from ta.momentum import RSIIndicator

import alerts.dispatcher as dispatcher_module
from alerts.triggers._dedup import (
    already_dispatched_for_symbol_on,
    symbol_in_morning_digest,
)
from alerts.triggers.intraday import (
    IV_SPIKE,
    SETUP_TRIGGERED,
    build_iv_spike_payload,
    build_setup_payload,
)
from core.config import get_settings
from core.logging import get_logger
from core.time import market_today, utcnow
from db.models.market import IndicatorDaily, Ticker
from ingestion.alpaca_client import QuoteRecord
from ingestion.iv import compute_atm_iv
from ingestion.options_client import OptionSnapshotRecord
from scheduler.context import job_run
from screener import pipeline as screener_pipeline
from screener.context import build_context
from screener.filters.base import FilterContext

log = get_logger(__name__)

JOB_NAME = "intraday_pulse"

QuoteSource = Callable[[list[str]], dict[str, QuoteRecord]]
ChainSource = Callable[[str], list[OptionSnapshotRecord]]

# Per-symbol throttle for the IV-spike pass — the chain pull is the
# expensive bit, so we avoid re-pulling within the configured interval.
# Module-level so it survives across ticks within a single process.
_LAST_IV_CHECK_AT: dict[str, datetime] = {}


def reset_iv_throttle() -> None:
    """Clear the per-symbol IV-check throttle. Used by tests."""
    _LAST_IV_CHECK_AT.clear()


def run_intraday_pulse(
    session: Session,
    *,
    quote_source: QuoteSource | None = None,
    chain_source: ChainSource | None = None,
    market_calendar: str | None = None,
    as_of: date | None = None,
    now: datetime | None = None,
) -> None:
    """One tick of the intraday pulse — see module docstring for the contract."""
    today = as_of or market_today()
    moment = now or utcnow()
    settings = get_settings()

    with job_run(session, JOB_NAME) as ctx:
        if market_calendar:
            schedule = _market_schedule(market_calendar, today)
            if schedule is None:
                log.info("intraday_pulse.holiday_skip", date=str(today))
                ctx.set_result(skipped="holiday", date=today.isoformat())
                return
            if not _within_rth(schedule, moment):
                log.info("intraday_pulse.outside_rth_skip", date=str(today))
                ctx.set_result(skipped="outside_rth", date=today.isoformat())
                return

        if quote_source is None:
            log.warning("intraday_pulse.no_quote_source")
            ctx.set_result(skipped="no_quote_source", date=today.isoformat())
            return

        symbols = _active_watchlist(session)
        if not symbols:
            ctx.set_result(skipped="no_symbols", date=today.isoformat())
            return

        quotes = quote_source(symbols)
        fresh = _fresh_quotes(quotes, max_age_s=settings.intraday_quote_max_age_s, now=moment)
        if not fresh:
            log.warning(
                "intraday_pulse.stale_quotes",
                requested=len(symbols),
                received=len(quotes),
                max_age_s=settings.intraday_quote_max_age_s,
            )
            ctx.set_result(
                skipped="stale_quotes",
                date=today.isoformat(),
                requested=len(symbols),
                received=len(quotes),
            )
            return

        setup_summary = _setup_pass(session, today=today, fresh=fresh)

        iv_summary = _IvSpikeSummary()
        if settings.intraday_iv_spike_enabled and chain_source is not None:
            iv_summary = _iv_spike_pass(
                session,
                today=today,
                fresh=fresh,
                chain_source=chain_source,
                threshold=settings.intraday_iv_spike_pct,
                throttle_minutes=settings.intraday_iv_spike_interval_minutes,
                now=moment,
            )

        ctx.set_result(
            as_of=today.isoformat(),
            symbols_evaluated=len(fresh),
            setup_fired=setup_summary.fired,
            setup_suppressed_morning=setup_summary.suppressed_morning,
            setup_suppressed_dedup=setup_summary.suppressed_dedup,
            iv_spike_fired=iv_summary.fired,
            iv_spike_suppressed=iv_summary.suppressed,
            iv_spike_checked=iv_summary.checked,
        )


# --- Setup pass --------------------------------------------------------------


class _SetupSummary:
    __slots__ = ("fired", "suppressed_dedup", "suppressed_morning")

    def __init__(self) -> None:
        self.fired = 0
        self.suppressed_morning = 0
        self.suppressed_dedup = 0


def _setup_pass(
    session: Session,
    *,
    today: date,
    fresh: dict[str, QuoteRecord],
) -> _SetupSummary:
    summary = _SetupSummary()
    configs = screener_pipeline._load_active_configs(session)
    if not configs:
        return summary

    for symbol, quote in fresh.items():
        intraday_close = quote.mid
        if intraday_close <= 0:
            continue

        base_ctx = build_context(session, symbol, today, include_options=False)
        if base_ctx is None or base_ctx.bars.empty:
            continue
        ctx = _synthesize_intraday_context(base_ctx, intraday_close=intraday_close, today=today)

        best_score: float | None = None
        best_config_name: str | None = None
        best_config_id: int | None = None
        best_eval_score: float | None = None
        for config in configs:
            evaluation = screener_pipeline._evaluate_symbol(ctx, config)
            if not evaluation.passed:
                continue
            score = evaluation.score if evaluation.score is not None else 0.0
            if best_score is None or score > best_score:
                best_score = score
                best_config_name = config.name
                best_config_id = config.id
                best_eval_score = evaluation.score
        if best_config_name is None:
            continue

        if symbol_in_morning_digest(session, as_of=today, symbol=symbol):
            log.info(
                "intraday_pulse.suppressed_morning",
                symbol=symbol,
                config=best_config_name,
            )
            summary.suppressed_morning += 1
            continue
        if already_dispatched_for_symbol_on(session, SETUP_TRIGGERED, as_of=today, symbol=symbol):
            summary.suppressed_dedup += 1
            continue

        rsi = ctx.indicators.get("rsi_14") if ctx.indicators is not None else None
        ivp = ctx.indicators.get("iv_percentile") if ctx.indicators is not None else None
        payload = build_setup_payload(
            symbol=symbol,
            as_of=today,
            config_name=best_config_name,
            config_id=best_config_id,
            close=intraday_close,
            score=best_eval_score,
            rsi=float(rsi) if rsi is not None and pd.notna(rsi) else None,
            iv_percentile=float(ivp) if ivp is not None and pd.notna(ivp) else None,
        )
        try:
            dispatcher_module.dispatch(SETUP_TRIGGERED, payload)
        except Exception as exc:  # pragma: no cover — dispatcher is best-effort
            log.warning("intraday_pulse.dispatch_failed", symbol=symbol, error=str(exc))
        else:
            summary.fired += 1

    return summary


# --- IV-spike pass -----------------------------------------------------------


class _IvSpikeSummary:
    __slots__ = ("checked", "fired", "suppressed")

    def __init__(self) -> None:
        self.fired = 0
        self.suppressed = 0
        self.checked = 0


def _iv_spike_pass(
    session: Session,
    *,
    today: date,
    fresh: dict[str, QuoteRecord],
    chain_source: ChainSource,
    threshold: float,
    throttle_minutes: int,
    now: datetime,
) -> _IvSpikeSummary:
    summary = _IvSpikeSummary()
    for symbol, quote in fresh.items():
        if not _ready_for_iv_check(symbol, now=now, throttle_minutes=throttle_minutes):
            continue
        baseline = _baseline_iv(session, symbol)
        if baseline is None or baseline <= 0:
            continue

        spot = quote.mid
        if spot <= 0:
            continue

        try:
            chain = chain_source(symbol)
        except Exception as exc:
            log.warning("intraday_pulse.chain_fetch_failed", symbol=symbol, error=str(exc))
            continue

        _LAST_IV_CHECK_AT[symbol] = now
        summary.checked += 1

        current = compute_atm_iv(chain, spot=spot, as_of=today)
        if current is None or current <= 0:
            continue

        pct_change = (current - baseline) / baseline
        if pct_change < threshold:
            continue

        if already_dispatched_for_symbol_on(session, IV_SPIKE, as_of=today, symbol=symbol):
            summary.suppressed += 1
            continue

        payload = build_iv_spike_payload(
            symbol=symbol,
            as_of=today,
            baseline_iv=baseline,
            current_iv=current,
            close=spot,
        )
        try:
            dispatcher_module.dispatch(IV_SPIKE, payload)
        except Exception as exc:  # pragma: no cover
            log.warning("intraday_pulse.iv_dispatch_failed", symbol=symbol, error=str(exc))
        else:
            summary.fired += 1

    return summary


def _ready_for_iv_check(symbol: str, *, now: datetime, throttle_minutes: int) -> bool:
    last = _LAST_IV_CHECK_AT.get(symbol)
    if last is None:
        return True
    return (now - last).total_seconds() >= throttle_minutes * 60


def _baseline_iv(session: Session, symbol: str) -> float | None:
    """Latest non-null ``iv_atm`` for ``symbol`` — typically yesterday's close."""
    row = session.execute(
        select(IndicatorDaily.iv_atm)
        .where(IndicatorDaily.symbol == symbol, IndicatorDaily.iv_atm.is_not(None))
        .order_by(IndicatorDaily.date.desc())
        .limit(1)
    ).first()
    return float(row[0]) if row and row[0] is not None else None


# --- Synthesis + helpers -----------------------------------------------------


def _synthesize_intraday_context(
    base: FilterContext,
    *,
    intraday_close: float,
    today: date,
) -> FilterContext:
    """Patch the base context with today's intraday close + a refreshed RSI(14).

    EMAs / Bollinger / HV / IV-derived indicators are kept frozen because
    one extra bar barely moves them and recomputing every indicator on every
    tick would make this loop materially more expensive without changing
    the alert outcomes for the configs we ship today.
    """
    bars = base.bars.copy()
    today_idx = pd.Timestamp(today)
    if today_idx in bars.index:
        existing = bars.loc[today_idx]
        bars.loc[today_idx, "high"] = max(float(existing["high"]), intraday_close)
        bars.loc[today_idx, "low"] = min(float(existing["low"]), intraday_close)
        bars.loc[today_idx, "close"] = intraday_close
    else:
        last_close = float(bars["close"].iloc[-1])
        bars.loc[today_idx, "open"] = last_close
        bars.loc[today_idx, "high"] = max(last_close, intraday_close)
        bars.loc[today_idx, "low"] = min(last_close, intraday_close)
        bars.loc[today_idx, "close"] = intraday_close
        bars.loc[today_idx, "volume"] = 0
        bars.sort_index(inplace=True)

    indicators = base.indicators.copy() if base.indicators is not None else None
    if indicators is not None and len(bars) >= 15:
        rsi_series = RSIIndicator(close=bars["close"].astype(float), window=14, fillna=False).rsi()
        latest_rsi = rsi_series.iloc[-1]
        if pd.notna(latest_rsi):
            indicators["rsi_14"] = float(latest_rsi)

    return FilterContext(
        symbol=base.symbol,
        as_of=base.as_of,
        bars=bars,
        indicators=indicators,
        options_chain=base.options_chain,
        earnings=base.earnings,
        ticker=base.ticker,
        macro=base.macro,
    )


def _fresh_quotes(
    quotes: dict[str, QuoteRecord], *, max_age_s: int, now: datetime
) -> dict[str, QuoteRecord]:
    fresh: dict[str, QuoteRecord] = {}
    for symbol, quote in quotes.items():
        ts = quote.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if (now - ts).total_seconds() <= max_age_s and quote.mid > 0:
            fresh[symbol] = quote
    return fresh


def _active_watchlist(session: Session) -> list[str]:
    rows = (
        session.execute(
            select(Ticker.symbol)
            .where(Ticker.is_active.is_(True), Ticker.is_hidden.is_(False))
            .order_by(Ticker.symbol)
        )
        .scalars()
        .all()
    )
    return list(rows)


def _market_schedule(calendar_name: str, day: date) -> Any | None:
    """Return today's market schedule row or ``None`` if the market is closed."""
    cal = mcal.get_calendar(calendar_name)
    schedule = cal.schedule(start_date=day, end_date=day)
    if schedule.empty:
        return None
    return schedule.iloc[0]


def _within_rth(schedule_row: Any, now: datetime) -> bool:
    open_ts = schedule_row["market_open"]
    close_ts = schedule_row["market_close"]
    open_dt = open_ts.to_pydatetime() if hasattr(open_ts, "to_pydatetime") else open_ts
    close_dt = close_ts.to_pydatetime() if hasattr(close_ts, "to_pydatetime") else close_ts
    if open_dt.tzinfo is None:
        open_dt = open_dt.replace(tzinfo=UTC)
    if close_dt.tzinfo is None:
        close_dt = close_dt.replace(tzinfo=UTC)
    return bool(open_dt <= now <= close_dt)
