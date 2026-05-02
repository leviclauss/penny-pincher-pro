# TODO

Living to-do list. Edit freely as work progresses — check items off, move
between sections, or delete entries that no longer matter. Git keeps the
history.

> **Convention.** `[ ]` = open, `[x]` = done. When checking something off,
> either leave it under "Recently done" for ~2 weeks of context, or delete
> it. Don't grow a permanent "done" graveyard.

---

## In progress

- [ ] **PR #3** — earnings + macro ingestion (open, validated against
      real APIs, awaiting merge)
- [ ] **PR #4** — scheduler + system API (open)
- [ ] **PR #5** — web UI dashboard + tickers (running in parallel
      session)

## Up next (after the open PRs land)

- [ ] **Scheduler picks up earnings + macro.** In
      `scheduler/app.py::_evening_entry`, build `FinnhubClient` +
      `YahooClient` and pass them to `run_evening_pipeline`. Add
      `earnings_rows` + `macro_rows` to the metrics dict. ~10 lines.
- [ ] **Silence httpx info logs.** Set
      `logging.getLogger("httpx").setLevel(WARNING)` in
      `core/logging.py`. The `HTTP Request: GET ...` lines clutter
      production output.
- [ ] **Frontend dashboard pulls real data.** Wire the new
      `/api/macro/current`, `/api/earnings/upcoming`, and the system
      `/api/system/job-runs` list into the dashboard panels.
- [ ] **Decide handoff to partner.** Schema is fully populated;
      partner can start `screener/filters/base.py`. Sync on which
      filters to build first (recommended: technical → volatility →
      event → economics, defer liquidity until paid options feed).

## Backlog — platform / infra

- [ ] **Sentry.** Free tier; drop the SDK in `core/logging.py` so the
      5:31 PM exception you'd otherwise miss gets paged.
- [ ] **FRED risk-free-rate refresh.** Quarterly script or cron to
      pull `TB3MS`. Currently hardcoded in `RISK_FREE_RATE`.
- [ ] **Tier-1 gating for options fetch.** Once the screener exists,
      gate options chain fetch on tier-1 pass — cuts API budget
      ~5–10x once the watchlist grows.
- [ ] **Watchlist curation UI.** Currently only `scripts/seed_dev`.
      Need a tier-assignment + bulk-CSV-import page.
- [ ] **Options snapshot freshness in `/api/system/health`.** Surface
      latest `snapshot_at` so the frontend can warn on staleness.
- [ ] **IV warm-up widget.** Dashboard tile showing "N days of IV
      history accumulated, X to go before rank/percentile go live."

## Backlog — alerts (doc 03)

- [ ] Trigger evaluators (`alerts/triggers/digest.py`,
      `alerts/triggers/setup.py`)
- [ ] Channel adapters: SMTP email, ntfy.sh push, webhook
- [ ] Dispatcher with dedup, rate limit, quiet hours
- [ ] HTML + plain-text templates
- [ ] Schedule morning + evening digest jobs
- [ ] End-to-end alert test against real channels before relying on
      it for trades

## Backlog — positions (doc 04)

- [ ] Manual entry CRUD API + Pydantic schemas
- [ ] State machine (short_put → long_shares → covered_call → closed)
- [ ] Daily snapshot job (mark-to-market from `options_snapshot`)
- [ ] Management rules (50% profit, 21 DTE, 0.45 delta, etc.)
- [ ] Performance attribution (per closed cycle, linked to
      `filter_config`)
- [ ] `/positions` UI pages

## Backlog — partner / screener (doc 02)

- [ ] `screener/filters/base.py` — Filter Protocol +
      FilterContext / FilterResult dataclasses
- [ ] `screener/registry.py` — id-string → class map
- [ ] Technical filters (`near_200ema`, `rsi_oversold`,
      `bb_lower_touch`, `not_freefall`, `weekly_above_200ema`)
- [ ] Volatility filters (`iv_rank_high`, `iv_percentile_high`,
      `iv_above_hv`) — fail-open until 126-day warm-up
- [ ] Event filters (`no_earnings_in_window`, `min_market_cap`,
      `tier_allowed`, `sector_concentration`)
- [ ] Economics (target strike at ~30 delta in 30–45 DTE, premium /
      annualized return / breakeven / expected-move)
- [ ] Liquidity (`option_spread_pct`); defer OI/volume filters
      until paid options feed
- [ ] `screener/pipeline.py` + wire `screener_run` step into the
      evening pipeline

## Backlog — backtesting (doc 06)

- [ ] Filter forward-return backtest (cheap, price-only)
- [ ] Synthetic Black-Scholes pricing for strategy backtest
- [ ] `backtest/strategy_backtest.py` full wheel sim
- [ ] Sharpe / drawdown / equity-vs-SPY metrics
- [ ] `/backtest` UI

## Backlog — frontend (doc 05, post-session-5)

- [ ] `/screener` (depends on partner's `screener_results`)
- [ ] `/configs` filter-config editor
- [ ] `/positions` (depends on positions session)
- [ ] `/alerts` history
- [ ] `/backtest` runner + results
- [ ] `/settings` (alert prefs, channel creds, refresh status)

## Pre-prod checklist (doc 08, before relying on real-money alerts)

- [ ] Backfill ≥ 1y bars for full intended watchlist (not just dev seed)
- [ ] Run evening pipeline 5 weekdays in a row successfully
- [ ] Cover at least one weekend + one holiday in that run
- [ ] Verify morning + evening digests deliver to chosen channels
- [ ] End-to-end position-alert test on a paper position
- [ ] DST transition handled correctly (test in both states)
- [ ] Force a run on a known holiday → confirm
      `skipped="holiday"` row + zero API calls
- [ ] Document SQLite recovery (corrupt-DB procedure)
- [ ] Document API-key rotation (Alpaca, Finnhub)
- [ ] Backups: nightly `sqlite3 .backup` + weekly off-site to B2/S3
- [ ] Verify a restore actually works
- [ ] healthchecks.io ping per scheduled job (alert if no run in 25h)
- [ ] Decide auth: HTTP basic via FastAPI dep, or Tailscale-only

## Out of scope for v1 (tripwires — discuss before adding)

- Auto-execution of trades
- Multi-leg options strategies (spreads, condors, strangles)
- Multi-user / OAuth auth beyond single-user basic auth
- Real-time streaming (30-min polling intraday is fine for v1)
- Backfilling historical options data (Alpaca history is too shallow;
  this would mean ORATS / CBOE — a paid-feed conversation, not code)

## Recently done

- [x] Session 1 — repo skeleton, schema, daily bars + indicators (PR #1)
- [x] Session 2 — option chains + ATM IV / rank / percentile (PR #2)
- [x] Session 3 — earnings (Finnhub) + macro (Yahoo VIX/SPY regime)
      (PR #3, validated)
- [x] Session 4 — APScheduler embedded; evening cron; `job_runs`
      logging; `/api/system/{health, job-runs, jobs/.../run}` (PR #4)
