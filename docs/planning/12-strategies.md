# 12 — Strategies

The word "strategy" is used in four different places across this codebase.
This doc maps each one to the code that implements it and the knobs that
tune it, so you can navigate without re-reading every module.

| Layer | What it means | Where it lives |
|---|---|---|
| Trading strategy | The single options strategy the system supports | [`backend/positions/state_machine.py`](../../backend/positions/state_machine.py) |
| Screener strategies | Named filter configs that surface candidates | [`backend/scripts/seed_filter_configs.py`](../../backend/scripts/seed_filter_configs.py) + [`backend/screener/`](../../backend/screener/) |
| Management strategies | Rules that fire alerts on open positions | [`backend/positions/management.py`](../../backend/positions/management.py) |
| Backtest strategies | Two distinct evaluation modes | [`backend/backtest/`](../../backend/backtest/) |

---

## 1. Trading strategy: the wheel

This is the only options strategy the system supports. Multi-leg
strategies (spreads, condors, strangles) are an explicit non-goal — see
[`docs/planning/00-overview.md`](00-overview.md).

A wheel cycle moves a single position through three states:

```
short_put ──assigned──▶ long_shares ──sell CC──▶ covered_call
    │                       ▲                         │
    │                       │                         │
    └─expired/closed─▶ closed                  expired/closed/called_away
```

States and transitions are enumerated in
[`backend/positions/state_machine.py`](../../backend/positions/state_machine.py):

- `STATE_SHORT_PUT` — short cash-secured put open
- `STATE_LONG_SHARES` — assigned (or seeded) shares held, no call written
- `STATE_COVERED_CALL` — shares held with a short call against them
- `STATE_CLOSED` — terminal state

Transition functions (`open_short_put`, `assign_short_put`,
`open_covered_call`, `called_away`, `expire_*`, `close_*`) validate the
current state, enforce leg invariants (e.g. enough shares to cover the
call), and compute realized P&L per leg. They mutate ORM rows but never
commit — the API/service layer owns the session.

Entry points are also seeded for cycles that don't begin with a CSP:
`open_long_shares` (already-held shares) and `open_covered_call_fresh`
(shares + a call, opened together).

For the full lifecycle spec see
[`docs/planning/04-position-tracking.md`](04-position-tracking.md).

---

## 2. Screener strategies

A "screener strategy" is a named **filter config** — a JSON object listing
filters, parameters, and per-filter scoring weights. The pipeline applies
these to every active ticker once a day; each config is independent and
ranks its own passers.

The catalog of available filters lives in
[`docs/planning/02-screener-filters.md`](02-screener-filters.md); the
runtime registry is
[`backend/screener/registry.py`](../../backend/screener/registry.py).

Six configs ship as defaults via
[`backend/scripts/seed_filter_configs.py`](../../backend/scripts/seed_filter_configs.py).
Each represents a different opinion on what "wheel-worthy" means:

| Config | Thesis | Key required filters | Top scoring weights |
|---|---|---|---|
| **Conservative Wheel — 200EMA Touch** | Quality names pulling back to long-term support in a high-IV regime | `weekly_above_200ema`, `no_earnings_in_window` (45d) | `iv_percentile_high` (0.35), `near_200ema` (0.25), `rsi_oversold` (0.25) |
| **Premium Hunter — High IV Rank** | Aggressive premium capture on uptrends with elevated IV | `weekly_above_200ema`, `iv_rank_high` (≥70), `no_earnings_in_window` (35d) | `iv_rank_high` (0.40), `iv_percentile_high` (0.25), `near_50ema` (0.20) |
| **Bollinger Bottom Reversal** | Mean-reversion entry: lower-band touch with rich IV | `bb_lower_touch`, `rsi_oversold` (≤35), `not_freefall`, `no_earnings_in_window` (30d) | `rsi_oversold` (0.40), `iv_percentile_high` (0.30), `iv_rank_high` (0.30) |
| **Blue Chip Income** | Tier-1 mega-caps only, modest premium, tight concentration | `weekly_above_200ema`, `tier_allowed=[1]`, `min_market_cap` (≥$50B), `no_earnings_in_window` (45d) | `iv_percentile_high` (0.40), `near_200ema` (0.30), `rsi_oversold` (0.30) |
| **Trend Pullback — 50EMA Bounce** | Bullish continuation: established uptrend pulling back to the 50 EMA | `weekly_above_200ema`, `near_50ema` (≤2.5%), `no_earnings_in_window` (35d) | `near_50ema` (0.40), `iv_percentile_high` (0.20), `iv_rank_high` (0.20), `rsi_oversold` (0.20) |
| **Volatility Spike Hunter** | Extreme IV regime — capitalize on vol mean-reversion via short-dated CSPs | `iv_rank_high` (≥75), `iv_above_hv` (≥1.15), `min_market_cap` (≥$10B), `no_earnings_in_window` (21d) | `iv_rank_high` (0.45), `iv_percentile_high` (0.30), `rsi_oversold` (0.25) |

Configs are first-class data — add new ones via the API or by extending
`ALL_CONFIGS` in `seed_filter_configs.py`. Each filter's defaults are
documented in [`docs/planning/02-screener-filters.md`](02-screener-filters.md);
adding a new filter is covered in `CLAUDE.md` ("How to add a screener
filter").

---

## 3. Management strategies

Once a position is open, the management pass evaluates each one daily and
fires an alert if any rule trips. Rules are defined in
[`backend/positions/management.py`](../../backend/positions/management.py)
and tunable via `ManagementConfig`. Defaults match
[`docs/planning/04-position-tracking.md`](04-position-tracking.md):

| Rule | Default threshold | Applies to | Intent |
|---|---|---|---|
| `pct_max_profit` | ≥ 50% of max profit captured | short put or covered call | Take profit early — premium decay flattens after this point |
| `dte` | ≤ 21 DTE | any open option leg | "Tastytrade rule" — close before gamma risk accelerates |
| `delta_breach` | abs(delta) ≥ 0.45 | short put only | Strike under threat — consider rolling or accepting assignment |
| `near_strike` | underlying within 2% of strike | any open option leg | Approaching breach — heads-up signal |
| `cc_itm_short_dte` | DTE ≤ 7 and underlying ≥ strike | covered call only | Roll or let assign decision point |
| `stale_position` | open > 60 days | the whole position | Lifecycle review — something probably went sideways |

Each fired rule produces one alert at most per position lifecycle, dedup
keyed on `(position_id, rule)`. A new wheel cycle is a fresh `Position`
row, so the dedup naturally resets between cycles.

Alert routing (Telegram / email / ntfy), templates, and quiet-hour
gating live in [`backend/alerts/`](../../backend/alerts/); the full
spec is [`docs/planning/03-alert-engine.md`](03-alert-engine.md).

---

## 4. Backtest strategies

Two distinct evaluation modes live behind `python -m backtest.cli`. The
mode selects what gets persisted and how. Reality-check caveats are in
[`docs/planning/06-backtesting.md`](06-backtesting.md).

### `--mode filter` — forward-return analysis

Implementation: [`backend/backtest/filter_backtest.py`](../../backend/backtest/filter_backtest.py)
+ [`forward_returns.py`](../../backend/backtest/forward_returns.py).

For every trading day in the window, replay the screener config
point-in-time and record the underlying's forward N-day return for each
passer. Each row in `backtest_trades` has `leg_type="filter_pass"`. Fast,
no option-pricing assumptions, useful for ranking configs against each
other.

Knobs: `--forward-days` (default 30), `--symbols`, `--calendar`.

### `--mode strategy` — full wheel simulator

Implementation: [`backend/backtest/simulator.py`](../../backend/backtest/simulator.py)
+ [`pricing.py`](../../backend/backtest/pricing.py)
+ [`portfolio.py`](../../backend/backtest/portfolio.py).

Each trading day the simulator:

1. Marks every open option leg via Black-Scholes against `iv_atm` /
   `hv_20` (or realized vol from bars as fallback).
2. Settles any leg expiring on/before today (ITM puts → shares, ITM
   calls → shares delivered, OTM → expire worthless).
3. Evaluates management rules — early close at `profit_take_pct` of max
   profit, roll/close at `manage_dte`.
4. Writes covered calls against any uncovered share lots (strike floored
   at cost basis).
5. Runs the screener config and opens new short puts on top-scoring
   passers within `max_concurrent_positions` and available cash.
6. Appends a `backtest_equity` row.

Each leg writes a `*_open` row when opened and a matching
`*_close`/`*_assigned`/`*_expired` row when it settles. Covered-call
assignments also emit a `share_sold` row so option premium P/L and
underlying-stock P/L stay on separate rows.

Knobs (defaults from [`simulator.py`](../../backend/backtest/simulator.py)):

| Flag | Default | Meaning |
|---|---|---|
| `--starting-capital` | 10,000 | Initial cash |
| `--max-concurrent-positions` | 5 | Cap on simultaneous open wheels |
| `--dte-target` | 30 | Target DTE for new short puts/calls |
| `--delta-target` | 0.30 | Magnitude of target delta for strike picking |
| `--profit-take-pct` | 0.50 | Close at this fraction of max profit |
| `--manage-dte` | 21 | Close/roll when DTE drops to this |
| `--fee-per-contract` | 0.65 | Commission per option contract per side |
| `--slippage-per-share` | 0.02 | Per-share slippage on each option fill |
| `--use-real-chain` | off | Price from `options_historical` instead of synthetic BS (requires the options-history backfill) |

Both modes are also reachable via `POST /api/backtest/runs` with
`mode: "filter" | "strategy"`; the API dispatches the simulator as a
FastAPI background task and clients poll `GET /api/backtest/runs/{id}`.
The `/backtest` page in the frontend exposes both via a mode tab.

---

## Source-of-truth links

- Wheel state machine: [`backend/positions/state_machine.py`](../../backend/positions/state_machine.py)
- Filter catalog: [`docs/planning/02-screener-filters.md`](02-screener-filters.md)
- Default screener configs: [`backend/scripts/seed_filter_configs.py`](../../backend/scripts/seed_filter_configs.py)
- Position management rules: [`backend/positions/management.py`](../../backend/positions/management.py)
- Position lifecycle spec: [`docs/planning/04-position-tracking.md`](04-position-tracking.md)
- Alert engine: [`docs/planning/03-alert-engine.md`](03-alert-engine.md)
- Backtest methodology: [`docs/planning/06-backtesting.md`](06-backtesting.md)
