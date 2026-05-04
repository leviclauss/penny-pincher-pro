# Penny Pincher Pro

A personal stock screener and alert system for the wheel options strategy
(cash-secured puts → covered calls). Alert-only — no automated execution.

## Status

All MVP tracks (00–08) from [`docs/planning/`](docs/planning/) have shipped.
The full schema (all 17 tables) was frozen up front by the initial Alembic
migration and is now actively used end-to-end.

- [x] **01 — Data ingestion.** Daily bars (split-adjusted) + indicators
      (EMA 20/50/200, RSI, ATR, weekly EMA200), option-chain snapshots
      with ATM IV / IV-rank / IV-percentile (Black–Scholes fallback),
      Finnhub per-symbol earnings, Yahoo VIX/VIX9D + SPY-vs-200EMA macro,
      and one-shot ticker-metadata refresh (sector, market cap).
- [x] **02 — Screener filters.** Tier 1–4 filter classes
      (technical / volatility / liquidity / event), `FILTER_REGISTRY`,
      a point-in-time `FilterContext` builder, and the pipeline
      orchestrator with weighted scoring + sector-concentration
      postprocessor that writes to `screener_results`. Daily
      `screener_pipeline` job runs after `evening_pipeline`. Read-only
      `/api/screener/configs` and `/api/screener/results` endpoints,
      plus a `/screener` UI page ranking candidates by score.
- [x] **03 — Alert engine.** Telegram + email + ntfy.sh channels behind
      a single dispatcher with quiet hours and per-type preferences.
      Phase 1 morning + evening digests, Phase 2 position-management
      triggers (50%/21 DTE/delta/etc. fired by the `position_management`
      job), and Phase 3 intraday `setup_triggered` + `iv_spike` pulses
      (off by default; opt-in via `SCHEDULER_INTRADAY_ENABLED`). All
      paths are holiday- / freshness- / dedup-guarded. Optional inbound
      Telegram bot supports `/status`, `/snooze`, and inline ack.
- [x] **04 — Position tracking.** Wheel state machine (CSP →
      assignment → CC → called away), daily `position_snapshots` pass,
      management-rule evaluator (50% profit / 21 DTE / delta breach /
      near-strike / CC ITM near expiry / stale), and a `position_management`
      scheduler job that fans triggers through the alert dispatcher.
      Positions can be grouped into portfolios.
- [x] **05 — Web UI.** Dashboard, `/tickers` + `/tickers/{symbol}`,
      `/screener` + `/screener/configs` (with editor), `/discovery`
      (S&P 100 scan), `/positions` + `/positions/{id}`, `/alerts`
      history with ack, `/backtest`, `/jobs`, and `/settings` for alert
      preferences. Responsive cards on narrow viewports.
- [x] **06 — Backtesting.** Filter forward-return CLI
      (`backtest.cli --mode filter`) and full-wheel strategy simulator
      (`--mode strategy`) using synthetic Black–Scholes pricing or
      `--use-real-chain` against the historical `options_historical`
      backfill. `/backtest` UI page with a per-trade detail dialog.
- [x] **07 — Scheduler & jobs.** APScheduler embedded in the FastAPI
      lifespan, `evening_pipeline` job, `job_run()` context manager
      writing every execution to `job_runs`, `/api/system/jobs` +
      `/api/system/job-runs` endpoints. Reliability hooks: nightly
      SQLite backups (with optional S3/B2 off-site upload),
      Healthchecks.io heartbeats per job, and on-failure alerts.
- [x] **08 — Deployment.** Backend + frontend Dockerfiles,
      `docker-compose.prod.yml`, CI builds prod images, Lightsail +
      Tailscale runbook in [`docs/deploy.md`](docs/deploy.md).

See [`CLAUDE.md`](CLAUDE.md) for conventions, the schema contract, and
how to add a filter or a new ingested source.

## Stack

- **Backend:** Python 3.11+, FastAPI, SQLAlchemy 2.x, Alembic, alpaca-py, `ta`
- **Storage:** SQLite (Postgres-compatible via SQLAlchemy)
- **Frontend:** React + Vite + Tailwind + shadcn/ui + TanStack Query
- **Tests:** pytest, pytest-asyncio
- **Lint/format:** ruff; **types:** mypy strict; **logs:** structlog

## Quick start

```bash
cp .env.example .env       # fill in Alpaca keys
make install               # backend + frontend deps
make migrate               # create SQLite schema
make ingest-full           # backfill 5y of bars + indicators (paper data ok)
make run-backend           # FastAPI on :8000
make run-frontend          # Vite on :5173
```

Or with Docker: `make dev` (runs backend + frontend via compose).

`FINNHUB_API_KEY`, `POLYGON_API_KEY`, and `TELEGRAM_BOT_TOKEN` /
`TELEGRAM_CHAT_ID` are optional but unlock earnings, options OI/volume +
historical chains, and notifications respectively. See `.env.example` for
all available knobs (email/ntfy channels, intraday pulse, backups,
healthchecks).

## Repo layout

```
backend/        FastAPI app, ingestion, screener, alerts, positions, ...
frontend/       Vite/React app
data/           SQLite DB lives here (gitignored)
docs/planning/  Design docs (the source of truth for schema and behavior)
```

## Common tasks

| Task | Command |
|---|---|
| Run tests | `make test` |
| Lint | `make lint` |
| Format | `make format` |
| Typecheck | `make typecheck` |
| New migration | `make migration m="add foo"` |
| Apply migrations | `make migrate` |
| Reset DB | `make db-reset` |
| Full ingestion | `make ingest-full` |
| Daily ingestion | `make ingest-incremental` |

## Contributing

Two tracks:
- **Platform/data/infra** — `ingestion/`, `api/`, `db/`, `scheduler/`, `core/`
- **Filters/strategy/backtesting** — `screener/`, `backtest/`

See [`CLAUDE.md`](CLAUDE.md) for the schema contract, how to add a filter,
and conventions both tracks follow.
