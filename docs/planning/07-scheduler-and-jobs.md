# 07 — Scheduler and Jobs

Jobs run on a fixed schedule (daily summaries) or in response to data changes (intraday alerts). Use APScheduler embedded in the FastAPI process for simplicity. If load grows, swap to Celery + Redis.

## Schedule (all times Pacific — adjust to your timezone)

| Job | Schedule | Purpose |
|---|---|---|
| `bars_incremental` | Mon–Fri 5:30 PM PT (after 4 PM ET close + buffer) | Pull today's daily bars |
| `indicators_compute` | After bars_incremental | Compute EMAs, RSI, BB, ATR for today |
| `options_snapshot` | After indicators (active tickers only) | Fetch chains for tickers passing tier 1+2 filters |
| `iv_compute` | After options_snapshot | Compute ATM IV, IVR, IVP |
| `screener_run` | After iv_compute | Run all active filter configs |
| `position_snapshot` | After options_snapshot | Update open position marks |
| `evening_digest` | 5:45 PM PT (after screener) | Send evening summary |
| `earnings_refresh` | Sundays 6 PM PT | Weekly refresh of earnings calendar |
| `macro_refresh` | Mon–Fri 5:25 PM PT | VIX, SPY, term structure |
| `morning_digest` | Mon–Fri 8:00 AM PT (1.5h before open) | Pre-market summary |
| `gap_check` | Mon–Fri 6:30 AM PT | Pre-market overnight gap detection |
| `intraday_screener` | Mon–Fri every 30m during RTH | Catch midday setups |
| `position_management_check` | Mon–Fri every 30m during RTH | Real-time position alerts |

## Job orchestration

Each job is a Python function with retry + logging:

```python
@scheduler.scheduled_job('cron', day_of_week='mon-fri', hour=17, minute=30, timezone='US/Pacific')
async def bars_incremental_job():
    with job_run('bars_incremental') as run:
        result = await ingestion.bars.fetch_incremental()
        run.set_result(symbols=len(result), bars=result.total_bars)
```

The `job_run` context manager:
- Logs start time
- Captures exceptions and triggers a system alert
- Records end time, duration, success/failure
- Persists to a `job_runs` table

## Job dependency chain

The evening sequence (post-close) has strict ordering. Use a single "evening pipeline" job that calls each step in sequence rather than scheduling each independently with hopeful timing:

```python
@scheduler.scheduled_job('cron', ..., hour=17, minute=30)
async def evening_pipeline():
    await bars_incremental()
    await macro_refresh()
    await indicators_compute()
    await options_snapshot()      # only for tickers passing tier 1
    await iv_compute()
    await screener_run()
    await position_snapshot()
    await evening_digest_send()
```

This way, if one step fails, downstream steps are skipped (or run on stale data with a clear warning) rather than silently working with bad inputs.

## Schema

```sql
job_runs (
  id INTEGER PRIMARY KEY,
  job_name TEXT,
  started_at DATETIME,
  ended_at DATETIME,
  status TEXT,                 -- 'running', 'success', 'failure'
  result_json TEXT,            -- arbitrary metrics
  error TEXT
)
```

Surface this table in the UI's `/settings` page so you can see at a glance: did everything run, was anything stale, what failed.

## Failure handling

- **Transient failures** (Alpaca 5xx, network blip): retry 3x with exponential backoff
- **Persistent failures**: send a system alert to your push channel, mark stale data in UI
- **Partial success**: e.g., bars fetched for 48 of 50 tickers — log which symbols failed, continue downstream with what we have, surface warning in digest

## Manual triggers

Every scheduled job is also exposed as `POST /api/system/jobs/{job_name}/run` for manual debugging from the UI. Good for: "I just added a ticker, run ingestion for it now."

## Holiday handling

Use `pandas_market_calendars` to know when markets are closed. Skip jobs on holidays. Half-days (early close): adjust evening pipeline to run earlier.

## Backfill mode

Separate from scheduler — a CLI command:

```
python -m scheduler.backfill --start 2024-01-01 --end 2024-12-31
```

Replays the evening pipeline for each historical date. Used to populate `screener_results` history for backtest comparisons or after schema changes.

## Module layout

```
scheduler/
  __init__.py
  app.py                   # APScheduler instance
  jobs/
    evening.py             # Evening pipeline
    morning.py             # Morning digest
    intraday.py            # Intraday screener + position checks
    weekly.py              # Earnings refresh
  context.py               # job_run context manager
  backfill.py              # CLI for historical replay
```

## Logging

Structured JSON logs (use `structlog`) so when something goes weird at 5:30 PM you can grep effectively. Log at INFO for job lifecycle, DEBUG for per-symbol details, WARN/ERROR for issues. Keep 30 days of logs locally.
