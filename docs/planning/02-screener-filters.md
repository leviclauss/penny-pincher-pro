# 02 — Screener Filters

A configurable filter pipeline that scores wheel candidates daily. Filters are composable, weightable, and stored as named configs the user can tune via the UI.

## Design principles

1. **Filters are pure functions:** `(ticker_data, config) -> FilterResult` where result includes pass/fail and a 0–1 score.
2. **Pipeline is data, not code.** A "filter config" is a JSON object listing filters and their thresholds. The user edits configs in the UI; code never needs to change to tune the screener.
3. **All filters write a row per ticker per day** so backtesting can replay the exact pass/fail decisions historically.

## Filter catalog

### Tier 1 — Trend / Mean Reversion

| Filter ID | Description | Default threshold |
|---|---|---|
| `near_200ema` | Close within X% of 200 EMA | 3% (configurable) |
| `near_50ema` | Close within X% of 50 EMA | 2% |
| `weekly_above_200ema` | Weekly close > weekly 200 EMA (regime filter) | required |
| `rsi_oversold` | Daily RSI(14) < threshold | 35 |
| `bb_lower_touch` | Close <= lower Bollinger Band | required |
| `not_freefall` | 5-day return > -10% (avoid catching knives) | -10% |

### Tier 2 — Volatility / Premium

| Filter ID | Description | Default threshold |
|---|---|---|
| `iv_rank_high` | IV Rank >= threshold | 50 |
| `iv_percentile_high` | IV Percentile >= threshold | 50 |
| `iv_above_hv` | IV(30) / HV(20) >= threshold (premium-rich) | 1.2 |

### Tier 3 — Options Liquidity

| Filter ID | Description | Default threshold |
|---|---|---|
| `option_spread_pct` | (ask-bid)/mid <= threshold at target strike | 0.10 |
| `option_oi_min` | Open interest at target strike >= threshold | 500 |
| `option_volume_min` | Avg daily option volume >= threshold | 100 |

### Tier 4 — Event / Risk

| Filter ID | Description | Default threshold |
|---|---|---|
| `no_earnings_in_window` | No earnings between today and target expiration | required |
| `min_market_cap` | Market cap >= threshold | $5B |
| `tier_allowed` | Ticker tier in allowed list | [1, 2] |
| `sector_concentration` | Don't fire alert if N candidates already passed in same sector today | max 3 |

### Tier 5 — Wheel-specific economics

For each candidate that passes Tiers 1–4, evaluate the target strike:

| Metric | Description |
|---|---|
| `target_strike` | Strike at ~30 delta (configurable: 20–35 delta range) |
| `target_dte` | Closest expiration in 30–45 DTE window (configurable) |
| `premium_pct_strike` | Credit / strike — proxy for annualized return |
| `annualized_return` | (premium / strike) * (365/DTE) — assuming not assigned |
| `breakeven` | Strike - premium |
| `breakeven_below_200ema_pct` | Margin of safety vs 200 EMA |
| `expected_move_dte` | IV-derived expected move over DTE — strike should be outside |

## Filter config example

```json
{
  "name": "Conservative Wheel - 200EMA Touch",
  "description": "High-IV pullbacks to long-term support on quality names",
  "filters": [
    {"id": "weekly_above_200ema", "required": true},
    {"id": "near_200ema", "params": {"max_pct": 0.03}},
    {"id": "rsi_oversold", "params": {"max_rsi": 40}},
    {"id": "iv_percentile_high", "params": {"min": 50}},
    {"id": "no_earnings_in_window", "params": {"days": 45}, "required": true},
    {"id": "min_market_cap", "params": {"min_usd": 10000000000}},
    {"id": "tier_allowed", "params": {"tiers": [1]}},
    {"id": "option_spread_pct", "params": {"max": 0.10}, "required": true},
    {"id": "not_freefall", "params": {"min_5d_return": -0.10}}
  ],
  "target_strike": {
    "method": "delta",
    "delta": 0.30,
    "dte_min": 30,
    "dte_max": 45
  },
  "scoring": {
    "weights": {
      "iv_percentile_high": 0.35,
      "near_200ema": 0.25,
      "premium_pct_strike": 0.25,
      "rsi_oversold": 0.15
    }
  }
}
```

## Scoring

After all required filters pass, optional filters contribute weighted scores. Final score is 0–100 for ranking on the dashboard. Sorting by score lets the morning summary show "top 10 wheel candidates today" cleanly.

## Schema additions

```sql
filter_configs (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE,
  description TEXT,
  config_json TEXT,            -- the JSON above
  is_active BOOLEAN,
  created_at DATETIME,
  updated_at DATETIME
)

screener_results (
  date DATE,
  symbol TEXT,
  config_id INTEGER,
  passed BOOLEAN,
  score REAL,
  filter_results_json TEXT,    -- per-filter pass/fail/value for debugging
  target_strike REAL,
  target_expiration DATE,
  target_premium REAL,
  target_delta REAL,
  annualized_return REAL,
  PRIMARY KEY (date, symbol, config_id)
)
```

Persisting per-filter results in `filter_results_json` is critical for the UI's "why didn't XYZ show up today?" debugging view, and for backtesting.

## Module layout

```
screener/
  __init__.py
  filters/
    __init__.py
    base.py              # Filter ABC, FilterResult dataclass
    technical.py         # EMA/RSI/BB filters
    volatility.py        # IV filters
    liquidity.py         # Option spread/OI filters
    event.py             # Earnings, market cap, tier
    economics.py         # Strike selection, premium calc
  pipeline.py            # Loads config, runs filters, persists results
  registry.py            # Maps filter ID strings to classes
```

## Multiple configs running in parallel

Run all active configs each evening — a single ticker can pass under one config and fail another. The UI groups results by config so you can compare strategies (e.g., "Conservative 200EMA" vs "Aggressive Oversold"). This is also how A/B testing of filter ideas happens in production without breaking the main flow.
