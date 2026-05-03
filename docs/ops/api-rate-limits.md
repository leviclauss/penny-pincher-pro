# API rate limits & external request budget

Snapshot of where outbound requests come from, where the ceilings are, and the
remediation plan we're working through. Last updated: 2026-05-03 (after the
PR #48 universe-scan rollout took the active ticker count from ~10 → ~110).

## Why this doc exists

PR #48 added the S&P 100 universe (101 symbols, inserted with `is_active=True,
is_hidden=True`). The three "active-only" symbol resolvers in ingestion don't
filter on `is_hidden`, so the nightly evening pipeline started treating
~111 symbols as full-fat ingestion targets — a ~10× increase in outbound
calls. The Finnhub free tier (60 cpm) was the first ceiling we hit.

## Daily request shape

| Step | Module | Pre-PR-#48 | Post-PR-#48 (no fix) | Post fix in this branch | Provider limit |
|---|---|---|---|---|---|
| Bars | `ingestion/bars.py` (50/req batch) | 1 req | ~3 reqs | ~3 reqs | Alpaca 200/min |
| Options chains | `ingestion/options.py` (1 req/symbol) | ~10 reqs | ~111 reqs | ~111 reqs | Alpaca 200/min |
| Earnings calendar | `ingestion/earnings.py` | ~10 reqs (per-symbol) | ~111 reqs (per-symbol, **no throttle**) | **1 req (bulk) + N fallback only for missing** | **Finnhub 60/min** |
| Macro (VIX/VIX9D) | `ingestion/macro.py` (Yahoo) | 2 reqs | 2 reqs | 2 reqs | n/a |
| Intraday pulse (off by default) | `scheduler/jobs/intraday.py` | watchlist only (`is_hidden=False`) | unchanged | unchanged | — |

The intraday pulse is safe today because `_active_watchlist` explicitly
excludes hidden tickers — but the evening pipeline resolvers don't. See
"Follow-ups" below.

## What this branch ships

1. **Bulk Finnhub earnings call** (`ingestion/earnings.py`)
   - One `calendar/earnings` request for the whole 90-day window, then
     filter to the active set in Python.
   - Per-symbol top-up for any active symbol missing from the bulk
     payload — preserves the correctness reason that motivated the
     original per-symbol loop (MSTR was historically dropped from bulk).
   - Toggle: `FINNHUB_EARNINGS_USE_BULK=false` to revert.

2. **Sliding-window rate limiter inside `FinnhubClient`**
   (`ingestion/finnhub_client.py::_RateLimiter`)
   - Caps outbound calls at `FINNHUB_RATE_LIMIT_PER_MIN` (default 55,
     just under the 60 cpm free-tier ceiling).
   - Defensive: even if a future change re-introduces a per-symbol burst
     pattern, we won't blow past the limit. Worst case, the call sleeps.

Net effect on Finnhub: **111 reqs/day → 1 req/day** in the happy path,
hard-capped at 55 cpm regardless of what the caller does.

## Why we didn't go further (yet)

Other options we evaluated but deferred — see PR description / chat log
for the full cost-benefit:

- **Skip ingestion for hidden universe tickers (option B).** The right
  long-term shape, but a bigger refactor. Touches `_resolve_symbols` in
  three modules plus the universe scan job. Worth doing once we're sure
  the universe is here to stay.
- **Cache earnings on a slower cadence for universe (D).** Largely
  redundant once bulk is in place — earnings go from "per-symbol-per-day"
  to "1-call-per-day" so frequency-based throttling barely matters.
- **Reduce universe options-chain refresh to weekly / M-W-F (E).** Still
  worth doing — Alpaca options are now ~111 req/day (sequential, ~3 sec
  between requests at our current pacing). Comfortably under 200 cpm but
  scales linearly with universe size. Recommended next step.
- **Parallelize options fetches (F).** Reduces wall-clock, not request
  count. Skip until pipeline runtime becomes a problem.
- **Pay for higher tiers (G).** Finnhub paid (~$50/mo, 300 cpm) and
  Alpaca Algo Trader Plus ($99/mo + paid options feed with volume / OI)
  would lift the ceiling and unlock the always-NULL volume / open_interest
  columns in `options_snapshot`. Not needed to fix the immediate
  breakage; revisit when the universe expands beyond S&P 100.

## Follow-ups (open work)

In rough priority order:

1. **Make `_resolve_symbols` honor `is_hidden`** in `bars.py`,
   `options.py`, `earnings.py`, then have `universe_scan` opt-in to the
   universe symbols it needs. This is the principled fix and will keep
   future universe expansions from re-creating the same problem.
2. **Reduce universe options refresh cadence to M/W/F or weekly** once
   (1) is in place. Watchlist stays nightly; universe gets refreshed less
   often since the screener tolerates a few-day-stale chain.
3. **Prometheus / structlog counter on Finnhub `_RateLimiter` sleeps.**
   If we ever hit it in production, we want to see it in the dashboards
   instead of finding out via a 429.
4. **Audit other hidden ticker assumptions.** The intraday pulse is the
   only path that currently filters hidden tickers; the dashboards /
   APIs should be reviewed if `is_hidden` semantics change.

## Configuration reference

| Setting | Default | What it does |
|---|---|---|
| `FINNHUB_RATE_LIMIT_PER_MIN` | `55` | Sliding-window cap inside `FinnhubClient`. Set to `60` to use the full free-tier budget; lower if you also use the same key elsewhere. |
| `FINNHUB_EARNINGS_USE_BULK` | `true` | When true, fetch earnings via 1 bulk call + per-symbol fallback. Set false to revert to the original per-symbol loop. |
| `FINNHUB_API_KEY` | unset | Earnings ingestion silently no-ops without a key. |
