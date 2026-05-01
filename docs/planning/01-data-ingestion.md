# 01 — Data Ingestion

Pull market data from Alpaca and supporting feeds, normalize, and persist. Everything downstream reads from the local DB, never directly from APIs.

## Data sources

| Data | Source | Frequency | Notes |
|---|---|---|---|
| Daily OHLCV bars | Alpaca `StockHistoricalDataClient` | Daily, post-close | Full history on first run, incremental after |
| Intraday bars (5m/15m) | Alpaca | Every 15 min during RTH | Optional for v1; powers intraday alerts |
| Options chains | Alpaca `OptionHistoricalDataClient` | On-demand per ticker | Used to evaluate strikes for screener output |
| Implied volatility | Computed from chain ATM | Daily | Alpaca doesn't expose IV directly — calculate via Black-Scholes |
| Earnings dates | Finnhub free tier | Weekly refresh | yfinance as fallback |
| VIX / macro | Alpaca (VIX is `^VIX` via index endpoint) | Daily | For regime filter |
| Sector / market cap | Static dataset + periodic refresh | Monthly | yfinance `Ticker.info` works, cache aggressively |

## Schema (SQLite, SQLAlchemy models)

```sql
-- Master ticker list with metadata
tickers (
  symbol TEXT PRIMARY KEY,
  name TEXT,
  sector TEXT,
  industry TEXT,
  market_cap REAL,
  is_active BOOLEAN,
  tier INTEGER,         -- 1=happy to own, 2=premium only, 3=avoid
  notes TEXT,
  added_at DATETIME,
  updated_at DATETIME
)

-- Daily bars
bars_daily (
  symbol TEXT,
  date DATE,
  open REAL, high REAL, low REAL, close REAL, volume INTEGER,
  PRIMARY KEY (symbol, date)
)

-- Computed indicators per symbol per day (denormalized for fast reads)
indicators_daily (
  symbol TEXT,
  date DATE,
  ema_20 REAL, ema_50 REAL, ema_200 REAL,
  ema_200_weekly REAL,
  rsi_14 REAL,
  atr_14 REAL,
  bb_upper REAL, bb_lower REAL, bb_mid REAL,
  iv_atm REAL,                -- computed from front-month ATM option
  iv_rank REAL,               -- (current - 52w low) / (52w high - 52w low)
  iv_percentile REAL,         -- % of days in past year IV was below current
  hv_20 REAL,                 -- realized 20d vol
  PRIMARY KEY (symbol, date)
)

-- Options snapshot (current chain — overwritten daily)
options_snapshot (
  symbol TEXT,
  expiration DATE,
  strike REAL,
  option_type TEXT,           -- 'call' or 'put'
  bid REAL, ask REAL, last REAL,
  volume INTEGER,
  open_interest INTEGER,
  delta REAL, gamma REAL, theta REAL, vega REAL,
  iv REAL,
  snapshot_at DATETIME,
  PRIMARY KEY (symbol, expiration, strike, option_type)
)

-- Earnings calendar
earnings (
  symbol TEXT,
  earnings_date DATE,
  time_of_day TEXT,           -- 'BMO', 'AMC', 'unknown'
  fetched_at DATETIME,
  PRIMARY KEY (symbol, earnings_date)
)

-- Macro indicators (single row per date for SPY/VIX context)
macro_daily (
  date DATE PRIMARY KEY,
  vix_close REAL,
  vix_9d REAL,
  vix_term_structure REAL,    -- VIX9D / VIX, < 1 = backwardation
  spy_close REAL,
  spy_ema_200 REAL,
  spy_above_200ema BOOLEAN
)
```

## Module layout

```
ingestion/
  __init__.py
  alpaca_client.py       # Wraps alpaca-py with retry/backoff
  bars.py                # Daily bar fetch + incremental updates
  indicators.py          # EMA/RSI/ATR/BB calculations (use `ta` lib)
  options.py             # Chain fetching, ATM IV computation
  earnings.py            # Finnhub + yfinance fallback
  macro.py               # VIX + SPY regime data
  pipeline.py            # Orchestrates a full ingestion run
```

## IV computation note

Alpaca returns greeks on options if you use the snapshot endpoint with the right parameters. If those aren't reliable, compute IV via py_vollib's `black_scholes` inversion using:
- Underlying spot
- Strike (use ATM — closest to spot)
- DTE
- Risk-free rate (3-month T-bill, refresh weekly from FRED or hardcode quarterly)
- Option mid price ((bid+ask)/2)

For "ATM IV" of a stock, take the front-month expiration that's at least 7 DTE out, find the nearest call and put to spot, and average their IVs.

## Ingestion run modes

- **`--full`** — backfill 2 years of bars + indicators for all tickers in the watchlist. Run once at setup or when adding new tickers.
- **`--incremental`** — fetch only new bars since last run + recompute indicators for the latest date. This is the daily job.
- **`--options-only`** — refresh options snapshots for active tickers (used by intraday alert checker).

## Rate limiting

Alpaca's free tier limits are ~200 requests/minute. Batch where possible:
- Bars: use `get_stock_bars` with multi-symbol requests (chunk into 50)
- Options: one request per symbol per expiration — this is the rate-limit-heavy piece, so cache aggressively and only refresh chains for symbols that passed Tier 1 of the screener that day

## Open questions to resolve before coding

1. **How big is the universe?** S&P 500? Russell 1000? Custom watchlist of ~50? Affects API budget significantly. For wheel, you probably want a curated 50-150 names you'd actually own.
2. **Subscription tier?** Free Alpaca data has 15-min delay and IEX-only. For real-time and full SIP, you need a paid feed. For daily summaries this doesn't matter — for intraday alerts it does.
3. **Historical options data?** Alpaca's options history is shallow. For backtesting (doc 06), this becomes a constraint to address separately.
