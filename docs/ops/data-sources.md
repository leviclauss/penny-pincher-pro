# External data sources

Where every row in the database comes from. Last updated: 2026-05-03.

If you find yourself asking "wait, who provides X again?" — this doc is the
answer. The companion docs are
[`api-rate-limits.md`](api-rate-limits.md) (request-budget reality) and
[`docs/planning/10-data-feed-options.md`](../planning/10-data-feed-options.md)
(tier-by-tier upgrade analysis).

## At a glance

| Provider | What it gives us | Tables it writes | Auth | Sole source? |
|---|---|---|---|---|
| **Alpaca** | Daily OHLCV bars; option chains (fallback) | `bars_daily`, `options_snapshot` | API key + secret | Bars: yes. Options: fallback only. |
| **Polygon** | Option chains (current + historical) | `options_snapshot`, `options_historical` | API key | Preferred for options; sole source for historical chains. |
| **Finnhub** | Earnings calendar; ticker profile (sector, market cap, name) | `earnings`, `tickers.sector` / `market_cap` / `name` | API key | Yes — no fallback. Silently no-ops if key missing. |
| **Yahoo Finance** | VIX & VIX9D index closes | `macro_daily.vix_close`, `vix_9d`, `vix_term_structure` | None (unauth scrape) | Yes |

Computed locally, not fetched: every column in `indicators_daily`,
`macro_daily.spy_*`, IV rank/percentile, all derived screener/backtest tables.
SPY's price data comes from Alpaca via `bars_daily`; the macro VIX/SPY combo
is assembled in `ingestion/macro.py` after the indicator step runs.

## Per-provider detail

### Alpaca — bars + (fallback) options

**Bars** (`ingestion/bars.py` → `ingestion/alpaca_client.py`)
- SDK: `alpaca-py` `StockHistoricalDataClient`
- Feed: configurable via `ALPACA_DATA_FEED` (default `iex`)
- Batch: 50 symbols/request
- Writes: `bars_daily` (date, open, high, low, close, volume, vwap)
- Limit: 200 req/min on the free tier; we sit at ~3 req/day full-universe

**Options** (`ingestion/options_client.py::AlpacaOptionsClient`)
- SDK: `alpaca-py` `OptionHistoricalDataClient`
- Feed: configurable via `ALPACA_OPTIONS_FEED` (default `indicative`)
- Writes: `options_snapshot` — but `volume` and `open_interest` are **always
  NULL** on the free tier. This is the reason Polygon exists in the picture.
- Selected only when `OPTIONS_PROVIDER=alpaca` (default), or when
  `OPTIONS_PROVIDER=polygon` is set but `POLYGON_API_KEY` is missing.

### Polygon — options (preferred when configured)

**Current chains** (`ingestion/options.py` → `ingestion/polygon_client.py`)
- HTTP wrapper around `api.polygon.io`
- Writes: `options_snapshot` — full payload **including** `volume` and
  `open_interest`
- Selected when `OPTIONS_PROVIDER=polygon` and `POLYGON_API_KEY` is set
- Limiter: `POLYGON_RATE_LIMIT_PER_MIN` (default 100, defensive cap; the
  Developer tier is effectively unlimited)

**Historical chains** (`ingestion/options_history.py`)
- One-shot backfill: `python -m ingestion.options_history --start ... --end ...`
- Writes: `options_historical` — daily per-contract OHLCV. The Developer tier
  doesn't expose historical bid/ask, so `close` is the stored mark.
- Powers two downstream features:
  - `python -m ingestion.iv_backfill` — seeds historical `iv_atm` so
    `iv_rank` / `iv_percentile` can be computed without waiting 126 days
  - Strategy backtest `--use-real-chain` (`backend/backtest/pricing.py::RealChainPricer`)

Polygon is the only provider in this stack with a paid-tier dependency for a
shipped feature (real-chain backtesting). If you drop Polygon, that mode
falls back to synthetic Black-Scholes pricing.

### Finnhub — earnings + ticker profile

**Earnings calendar** (`ingestion/earnings.py` → `ingestion/finnhub_client.py`)
- HTTP wrapper around `finnhub.io/api/v1`
- Writes: `earnings` (90-day forward window per `EARNINGS_LOOKAHEAD_DAYS`)
- Default mode: per-symbol fetch, throttled to `FINNHUB_RATE_LIMIT_PER_MIN`
  (default 55, just under the 60 cpm free-tier ceiling)
- Bulk mode (`FINNHUB_EARNINGS_USE_BULK=true`) is **off by default** — see
  [`api-rate-limits.md`](api-rate-limits.md) for why
- US equities only; ETFs (SPY, QQQ) silently return nothing
- **No fallback**: without `FINNHUB_API_KEY` the earnings step no-ops and
  the daily pipeline keeps going. Earnings filters in the screener will then
  silently never fire.

**Ticker metadata** (`ingestion/ticker_metadata.py`)
- One-shot: `python -m ingestion.ticker_metadata`
- Writes: `tickers.name`, `sector` (from `finnhubIndustry`), `market_cap`
- Free tier returns nothing for ETFs, so SPY/QQQ rows keep NULL sector
- Not part of the daily pipeline — refresh manually when adding tickers or
  when sector classifications drift

### Yahoo Finance — VIX index data

**Macro indices** (`ingestion/macro.py` → `ingestion/yahoo_client.py`)
- Unauthenticated HTTP against `query1.finance.yahoo.com`
- Writes: `macro_daily.vix_close`, `vix_9d`, `vix_term_structure`
  (= `vix_9d / vix_close`; <1 means backwardation per doc 01)
- 2 requests/day total — VIX and VIX9D
- The rest of `macro_daily` is computed locally:
  - `spy_close` / `spy_ema_200` / `spy_above_200ema` come from
    `bars_daily` + `indicators_daily` (Alpaca, not Yahoo)
- Risk: unauth scraping. Yahoo can change response shape or block us
  without notice. Low blast radius — if it breaks, only the macro regime
  pill on the dashboard goes stale.

## Daily pipeline ordering

`ingestion/pipeline.py::run_pipeline` runs the steps in this order, each one
holiday-skipped and individually toggleable via `--skip-*`:

1. **Bars** (Alpaca) — must run first; everything else reads from it
2. **Indicators** (local) — EMAs, RSI, ATR, HV; reads `bars_daily`
3. **Options** (Polygon or Alpaca) — current chain snapshot; replaces prior
   rows so stale strikes don't linger
4. **IV** (local) — `iv_atm` from the chain; `iv_rank`/`iv_percentile` from
   the rolling 252-day history
5. **Earnings** (Finnhub) — 90-day forward window
6. **Macro** (Yahoo + local) — Yahoo for VIX/VIX9D, `bars_daily`/`indicators_daily`
   for SPY. Must run after step 2.

Each step writes a `job_runs` row via `scheduler.context.job_run()`.

## Configuration cheat sheet

| Setting | Default | Affects |
|---|---|---|
| `ALPACA_API_KEY` / `ALPACA_API_SECRET` | unset | Bars (required); Alpaca options (required when used as fallback) |
| `ALPACA_DATA_FEED` | `iex` | Bar feed; SIP requires paid tier |
| `ALPACA_OPTIONS_FEED` | `indicative` | Options feed when Alpaca is the provider |
| `OPTIONS_PROVIDER` | `alpaca` | Switch to `polygon` to get volume + OI |
| `POLYGON_API_KEY` | unset | Required if `OPTIONS_PROVIDER=polygon`; pipeline falls back to Alpaca if missing |
| `POLYGON_RATE_LIMIT_PER_MIN` | `100` | Defensive cap; Developer tier is effectively unlimited |
| `FINNHUB_API_KEY` | unset | Earnings + ticker metadata (silently no-ops without it) |
| `FINNHUB_RATE_LIMIT_PER_MIN` | `55` | Sliding-window cap inside `FinnhubClient` |
| `FINNHUB_EARNINGS_USE_BULK` | `false` | Off — bulk silently drops some reports. See `api-rate-limits.md`. |
| `EARNINGS_LOOKAHEAD_DAYS` | `90` | Forward window for the earnings calendar |
| `RISK_FREE_RATE` | `0.045` | Used by IV inversion (py_vollib) and synthetic Black-Scholes pricing |

## Failure modes & fallbacks

| Provider down/misconfigured | What breaks | What still works |
|---|---|---|
| Alpaca | Bars step fails → entire pipeline halts (everything reads from `bars_daily`) | Nothing — bars are load-bearing |
| Polygon (with `OPTIONS_PROVIDER=polygon`) | Pipeline auto-falls back to Alpaca options (no volume/OI) | Bars, IV, earnings, macro |
| Polygon (historical backfill) | `iv_backfill` and `--use-real-chain` backtests unavailable | Daily pipeline (current chains only) |
| Finnhub | Earnings step no-ops; earnings filters silently never fire; sector/market_cap stay NULL on new tickers | Bars, options, IV, macro |
| Yahoo | Macro VIX columns stay NULL; dashboard regime pill goes stale | Everything else; SPY-side macro keeps working |

## When to revisit this

- Adding a new provider — add a row to the at-a-glance table and a section
  below
- Changing the default for `OPTIONS_PROVIDER` — update both this doc and
  `CLAUDE.md`'s schema notes
- Promoting any "fallback" relationship to an A/B comparison — that's the
  point at which the `OptionsClient` protocol pattern should be
  generalized to other domains (bars, earnings) rather than hand-rolled
  per provider
