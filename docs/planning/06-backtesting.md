# 06 — Backtesting

## Implementation status

**v0 (shipped):** filter forward-return backtest, exposed as a CLI
(`python -m backtest.cli --mode filter`). One row per evaluation day per
passing symbol lands in `backtest_trades` (`leg_type="filter_pass"`); a
`backtest_runs` row records the config + window. See
[`backend/backtest/filter_backtest.py`](../../backend/backtest/filter_backtest.py)
and [`backend/backtest/forward_returns.py`](../../backend/backtest/forward_returns.py).

**v1 (shipped):** full wheel strategy simulator (cash-secured put → covered
call lifecycle) with synthetic Black-Scholes pricing, capital management,
and equity-curve writes to `backtest_equity`. Run via
`python -m backtest.cli --mode strategy --starting-capital 10000 ...`. See
[`backend/backtest/simulator.py`](../../backend/backtest/simulator.py),
[`backend/backtest/pricing.py`](../../backend/backtest/pricing.py), and
[`backend/backtest/portfolio.py`](../../backend/backtest/portfolio.py).

**v2 (shipped):** strategy-mode wiring across the read API and UI. The
launcher (`POST /api/backtest/runs`) accepts `mode: "strategy"` plus a
`strategy_params` payload, pre-creates the run row in `running` state, and
dispatches the simulator via FastAPI background tasks (returning 202 +
the run snapshot). Status flips to `completed` or `failed` (with
`error_message` populated) when the background task finishes; clients
poll `GET /api/backtest/runs/{id}`. Equity curve is exposed at
`GET /api/backtest/runs/{id}/equity`. The `/backtest` page in the
frontend offers a Filter/Strategy mode tab, a strategy-params form,
auto-polling, an equity chart, and a leg-type-filterable trade table.

**Deferred:** multi-contract sizing per position, sensitivity sliders on
the UI, SPY benchmark overlay on the equity chart.

---

Two distinct backtests, often confused:

1. **Filter backtest** — given historical filter values, how often did "passes" lead to favorable outcomes for the underlying over the next N days?
2. **Strategy backtest** — full wheel simulation: sell put → manage → assign or close → sell call → repeat, with realistic P&L.

The first is fast and uses only price data. The second requires historical option pricing, which is the hard part.

## The historical options data problem

Alpaca's options history is shallow — they only added options data relatively recently, and historical chains are limited. For meaningful backtesting you have a few paths:

| Option | Cost | Quality |
|---|---|---|
| Alpaca's own options history | Included | Limited lookback, decent for recent periods |
| ORATS Data API | Paid (~$100+/mo) | Excellent — full chains, IV surfaces |
| CBOE DataShop | Paid, one-time | Definitive but expensive |
| Approximate via Black-Scholes | Free | Use historical IV from Alpaca + BS to synthesize option prices. Imperfect but workable for wheel mechanics |

**Recommendation:** start with synthetic prices via Black-Scholes (using historical realized vol or IV proxies), validate against any real Alpaca options snapshots you have, and only upgrade to paid data if backtests show real promise. Wheel strategies are sensitive enough to vol assumptions that you'll want to be honest about the model's limitations.

## Filter backtest

### Methodology

For each historical date in the test period:
1. Compute filter values as they would have been *that day* (point-in-time)
2. Determine which tickers passed the chosen config
3. Look forward N days (e.g., 30, 45) and measure underlying outcome:
   - % win rate (close > entry, or didn't break entry - 5%)
   - Average drawdown over window
   - % of cases where underlying touched a hypothetical 30-delta strike

### Avoiding lookahead bias

This is non-negotiable. For each historical date:
- Indicators must use only data up to and including that date
- Earnings calendar must reflect what was *known* on that date (use the earliest fetch timestamp you have for each earnings record)
- IV rank/percentile use a 252-day rolling window ending on that date
- Tier assignments — if these change over time, version them

### Output

Per-config summary:
- N candidates surfaced over period
- Forward-N-day return distribution (mean, median, std, percentiles)
- % outcomes that would have resulted in profitable wheel cycles (proxy)
- Hit rate by filter — which filters added most signal

Visualize as a calendar heatmap + summary table on `/backtest`.

## Strategy backtest

Full wheel simulation. More complex.

### Simulator core loop

```
state = {capital, positions=[], cash, history=[]}

for each trading day in test period:
  for each open position:
    update mark-to-market
    evaluate management rules
    if rule triggered: close/roll position

  run screener for the day
  for each candidate (top N by score, capped by capital):
    if cash sufficient for cash-secured put:
      open short put position at modeled strike/premium

  # End of day
  record state snapshot
```

### Modeling decisions to make explicit

- **Entry price for options:** mid? mid + slippage assumption? Mid + 50% of spread is realistic
- **Commissions:** $0.65/contract for most retail brokers — model it
- **Assignment rules:** ITM by $0.01 at expiry → assigned. Early assignment can be ignored for naked puts (rare without dividends)
- **Slippage on close:** 1-3 cents per share is reasonable for liquid names
- **Capital constraint:** track total cash-secured collateral; new puts only if cash available

### Output metrics

- Total return / annualized return / Sharpe
- Max drawdown
- Win rate per cycle
- Avg cycle days
- Time spent in CSP vs holding shares vs CC
- "Tail risk" — worst 5% of cycles by P&L
- Equity curve vs SPY buy-and-hold benchmark

### Schema

```sql
backtest_runs (
  id INTEGER PRIMARY KEY,
  config_id INTEGER,
  start_date DATE, end_date DATE,
  starting_capital REAL,
  params_json TEXT,            -- DTE target, delta target, mgmt rules
  created_at DATETIME
)

backtest_trades (
  run_id INTEGER,
  cycle_id INTEGER,
  symbol TEXT,
  leg_type TEXT,
  entry_date DATE, exit_date DATE,
  strike REAL, expiration DATE,
  entry_price REAL, exit_price REAL,
  outcome TEXT,
  realized_pnl REAL,
  fees REAL
)

backtest_equity (
  run_id INTEGER,
  date DATE,
  equity REAL,
  cash REAL,
  collateral_locked REAL,
  unrealized_pnl REAL,
  PRIMARY KEY (run_id, date)
)
```

## UI: `/backtest`

- Pick filter config + date range + starting capital + management params
- "Run" → background job, polls for completion
- Results page:
  - Equity curve vs SPY
  - Trade list with filter + sort
  - Per-cycle detail
  - Sensitivity sliders: vary delta target, DTE target, profit-take % → see P&L change without re-running (cached if pre-computed)

## Module layout

```
backtest/
  __init__.py
  data/
    point_in_time.py     # Fetch indicator values as of date X
    synthetic_options.py # Black-Scholes pricing from historical IV
  filter_backtest.py     # Forward-return analysis
  strategy_backtest.py   # Full simulator
  metrics.py             # Sharpe, drawdown, etc.
  api.py                 # Routes for triggering/polling
```

## Reality check

Backtests will always look better than live performance because:
- Synthetic option pricing is optimistic (no real bid-ask)
- Survivorship bias in your watchlist
- Earnings surprises and tail events are hard to model

Treat backtest results as **filter ranking** ("config A consistently looks better than config B") rather than **return prediction** ("this will make 18% annualized"). The system's most valuable use is comparing configs head-to-head over the same period, not promising returns.
