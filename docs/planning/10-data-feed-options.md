# 10 — Data Feed Options & Intraday Strategy

This doc captures the analysis behind the intraday refresh job and surveys
richer data feeds we could move to if/when free-tier limitations become
binding. The product spec (docs 00–08) assumes Alpaca free + Finnhub free
+ Yahoo for VIX. This doc is the place to revisit that assumption.

## The question that prompted this

> Are alerts even useful if we don't have a live data feed?

Short answer: **yes, but only if we refresh during the trading session.**
End-of-day alerts are useless for the wheel — IV crush, premium spikes,
and "underlying just touched my put strike" are all intraday events that
are gone or decayed by the time the evening pipeline runs.

The alert engine's job is to surface **opportunities the user can act on
within the same session**, not to report what already happened.

## What "live" actually needs to mean for this product

This is alert-only, single-user, no automated execution. That puts us in
a very different latency regime than an HFT or even a discretionary
day-trading setup:

| Use case | Latency budget | Notes |
|---|---|---|
| CSP entry alert ("juicy premium on TICKER") | 5–30 min | Premium opportunities last minutes-to-hours, not seconds |
| Underlying touches strike | 5–15 min | User isn't going to manually roll in 30 sec anyway |
| IV regime change (VIX spike) | 15–60 min | Macro decisions are slow |
| Earnings within X days | daily | Calendar-driven, no urgency |
| Position management (21 DTE roll, 50% profit) | 1–24 hours | These are checks, not reactions |

**Conclusion:** a 15-minute polling cadence is sufficient for every alert
this product currently plans to emit. Streaming is overkill.

## Free-tier reality check (Alpaca + Finnhub + Yahoo)

| Capability | Free tier | Limits | Impact on this project |
|---|---|---|---|
| Stock bars (daily) | IEX feed, real-time | 200 req/min | Fine — 50-ticker watchlist polls in one batch |
| Stock bars (1-min, intraday) | IEX feed, real-time | Same | Available if we want it; we don't currently store 1-min |
| Stock bars (SIP / consolidated tape) | ❌ paid | — | IEX is ~2–3% of consolidated volume; thinner quotes for less-liquid names |
| Options snapshots | OPRA Indicative, ~15-min delayed | 200 req/min | Workable; bid/ask/IV/greeks present |
| Options volume / open interest | ❌ not on free snapshot endpoint | — | Hard liquidity filters degraded — see doc 02, "Tier 2 - Liquidity" |
| Options historical chains | ❌ shallow | — | Backtests use synthetic Black-Scholes pricing (doc 06) |
| Options websocket streaming | ❌ paid | — | Not needed at 15-min cadence |
| Earnings calendar (Finnhub) | US equities, ~90 days forward | 60 req/min | Adequate |
| VIX / VIX9D (Yahoo) | Real-time index level | Polite scraping only | Adequate; not load-bearing |

**The two real bites of the free tier:**

1. **Options data is 15-min delayed.** This caps alert latency at 15 min
   regardless of how often we poll. This is the dominant constraint.
2. **No options volume / open interest on the free snapshot endpoint.**
   Means we can't enforce strict liquidity filters (e.g., "OI > 500" or
   "volume > 100 today"). We can use bid/ask spread as a proxy, but
   illiquid contracts can still slip through.

Rate limits are **not** a real constraint at this scale. A 50-ticker
watchlist polled every 15 min during a 6.5-hour session is ~26 polls/day
× 50 tickers = 1,300 calls/day, well under 200 req/min on either feed.

## Decision: poll every 15 min, free tier, for now

The intraday refresh job (planned in `backend/scheduler/jobs/intraday.py`)
runs every 15 min during NYSE RTH and calls `run_incremental(...,
skip_earnings=True, skip_macro=True)`. This:

- Refreshes today's daily bar (IEX-aggregated, real-time).
- Recomputes today's indicator row.
- Replaces today's options snapshot (15-min delayed but acceptable).
- Recomputes today's ATM IV.

Earnings and macro stay daily — they don't move intraday in any way that
matters for this product.

The job is opt-in via `SCHEDULER_INTRADAY_ENABLED` so it stays off until
the alert engine actually consumes the freshened data.

## When to reconsider — upgrade triggers

Move off free tier only when one of these is **actually blocking**:

1. **A liquidity filter we want is impossible** without volume/OI.
   Specifically: if backtests show that "OI > 500" materially improves
   trade quality and we're systematically getting bad fills without it.
2. **The 15-min options delay is producing demonstrably stale alerts.**
   Measured, not hypothetical — log the gap between alert-trigger time
   and "would have been correct at" time, and see if it matters.
3. **We start trading less-liquid underlyings** where IEX-only quotes
   diverge meaningfully from SIP. Most wheel candidates (S&P 500
   liquid names) are fine on IEX.

If none of these are true, every dollar spent on a paid feed is wasted.

## Richer data feed options (if/when we upgrade)

Sketched in rough order of fit. Prices are public list prices as of
2026-05; verify before committing.

### Tier A — Drop-in upgrades to current providers

#### Alpaca paid tiers

- **Algo Trader Plus (~$99/mo):** SIP stock data, full options snapshot
  with volume/OI, historical options chains.
- **Pros:** zero code changes — same SDK, same shape, same client wrapper
  (`alpaca_client.py`, `options_client.py`). Just flip the feed flag.
- **Cons:** still no true real-time options quotes (15-min delay may
  remain on the snapshot endpoint — verify); options history depth is
  still shallow vs. dedicated vendors.
- **When this is right:** we want volume/OI and SIP stocks, don't care
  about deep historical options.

#### Finnhub paid

- Adds intraday earnings, broader fundamentals, news sentiment.
- Probably not worth it for this product — earnings calendar is the
  only Finnhub dependency and the free tier covers it.

### Tier B — Dedicated options data vendors

Worth it if/when backtesting becomes the limiting factor.

#### ORATS

- **Strength:** purpose-built for options analytics — full historical
  chains, IV surfaces, term structures, smoothed IV. This is what
  serious options backtesters use.
- **Cost:** Datafiles tier ~$200–500/mo depending on history depth.
- **Integration cost:** new `ingestion/orats_client.py`, schema for
  historical chains (we deferred this in v1 — see doc 06).
- **When this is right:** we're investing seriously in backtesting and
  the synthetic BS pricing approach is producing misleading results.

#### CBOE LiveVol / DataShop

- **Strength:** authoritative source (CBOE owns the exchange). Highest
  data quality, deepest history.
- **Cost:** enterprise-tier, not realistic for personal use.
- **When this is right:** never, for this product.

#### Theta Data

- **Strength:** cheapest credible historical options vendor (~$80/mo
  for standard tier). REST API, bulk downloads.
- **Cons:** smaller team, less polished SDK, occasional gaps.
- **When this is right:** budget-conscious backtesting upgrade.

### Tier C — Brokerage-bundled feeds

These come "free" with a brokerage account, but require running their
client to pull data — ops burden goes up.

#### Tradier

- Real-time options data included with brokerage account (~$10/mo for
  market data alone, free with active trading).
- Decent REST API. No execution dependency — you can pay for data
  without trading there.
- **When this is right:** we want real-time options without the Alpaca
  paid subscription.

#### IBKR / Schwab (thinkorswim) / Tastytrade

- All offer real-time data with an account. APIs vary wildly:
  - **IBKR:** powerful, ugly. TWS gateway must run locally. High ops
    burden but most flexible.
  - **Schwab:** post-TDA migration; API is workable but rate-limited.
  - **Tastytrade:** clean modern API, options-focused, free with
    account, no minimums. **This is likely the best brokerage-feed
    option for this product** if we ever execute trades there.
- **When this is right:** we're moving execution to one of these brokers
  anyway and can fold data into the same integration.

### Tier D — Streaming / websocket

- **Polygon.io Options Starter (~$29/mo) + Advanced (~$199/mo):**
  websocket streaming, real-time quotes, full chains with greeks. The
  closest thing to "professional" data at retail pricing.
- **Alpaca paid websocket:** same as REST paid tier, with streaming.
- **When this is right:** we add an alert type that genuinely needs
  sub-minute latency — e.g., "fill on this exact strike within X
  seconds." The current alert spec (doc 03) does not require this.

## Recommended progression

1. **Now:** free Alpaca + 15-min intraday job. Build the alert engine
   against this. Measure actual alert latency and whether it produces
   actionable signals.
2. **First upgrade (~$99/mo Alpaca Plus):** when we want volume/OI for
   liquidity filters or SIP stock data. Zero code changes beyond a
   feed flag.
3. **Backtesting upgrade (~$80–200/mo Theta Data or ORATS):** when
   synthetic BS pricing in doc 06 is demonstrably misleading and we
   want real historical chains. New ingestion module + schema for
   `options_history`.
4. **Streaming (Polygon or Tastytrade):** only if a future alert type
   actually requires sub-minute latency. Not on the current roadmap.

## Cross-references

- Intraday job implementation: `backend/scheduler/jobs/intraday.py`
  (planned).
- Alert engine that consumes intraday data: doc 03.
- Backtesting strategy and synthetic pricing rationale: doc 06.
- Liquidity filter assumptions: doc 02, "Tier 2 — Liquidity."
- Free-tier caveats elsewhere documented: `CLAUDE.md` schema notes on
  `options_snapshot.volume` / `open_interest`.
