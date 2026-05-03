# Penny Pincher Pro

A personal stock screener and alert system for the wheel options strategy
(cash-secured puts → covered calls). Alert-only — no automated execution.

## Status

Roadmap follows the implementation order in [`docs/planning/`](docs/planning/)
(00–08). The full schema (all 17 tables) is frozen by the initial Alembic
migration, so unbuilt tracks already have their storage contract.

- [x] **01 — Data ingestion.** Daily bars (split-adjusted) + indicators
      (EMA 20/50/200, RSI, ATR, weekly EMA200), option-chain snapshots
      with ATM IV / IV-rank / IV-percentile (Black–Scholes fallback),
      Finnhub per-symbol earnings, Yahoo VIX/VIX9D + SPY-vs-200EMA macro,
      and one-shot ticker-metadata refresh (sector, market cap).
- [x] **05 — Web UI (read-only slice).** Dashboard (macro strip,
      watchlist freshness, upcoming earnings, VIX history), `/tickers`
      sortable watchlist, `/tickers/{symbol}` detail (1y price chart with
      EMA 20/50/200 + earnings markers, RSI(14), IV history), plus
      watchlist add/edit/hide/delete with per-ticker backfill.
- [x] **07 — Scheduler & jobs.** APScheduler embedded in the FastAPI
      lifespan, `evening_pipeline` job, `job_run()` context manager
      writing every execution to `job_runs`, `/api/system/jobs` +
      `/api/system/job-runs` endpoints.
- [x] **08 — Deployment.** Backend + frontend Dockerfiles,
      `docker-compose.prod.yml`, CI builds prod images, Lightsail +
      Tailscale runbook in [`docs/deploy.md`](docs/deploy.md).
- [x] **02 — Screener filters.** Tier 1–4 filter classes
      (technical / volatility / liquidity / event), `FILTER_REGISTRY`,
      a point-in-time `FilterContext` builder, and the pipeline
      orchestrator with weighted scoring + sector-concentration
      postprocessor that writes to `screener_results`. Daily
      `screener_pipeline` job runs after `evening_pipeline`. Read-only
      `/api/screener/configs` and `/api/screener/results` endpoints,
      plus a `/screener` UI page ranking candidates by score.
- [x] **04 — Position tracking.** Wheel state machine (CSP →
      assignment → CC → called away), daily `position_snapshots` pass,
      management-rule evaluator (50% profit / 21 DTE / delta breach /
      near-strike / CC ITM near expiry / stale), and a `position_management`
      scheduler job that fans triggers through the alert dispatcher.
- [~] **03 — Alert engine.** _Phase 1 — daily digests:_ Telegram
      channel + dispatcher (with quiet hours and per-type preferences),
      morning + evening digest builders, scheduled `morning_digest` /
      `evening_digest` jobs with holiday + stale-data + dedup guards.
      _Remaining:_ position-management triggers (Phase 2) and
      intraday setup / IV-spike triggers (Phase 3).
- [ ] **06 — Backtesting.** `backtest/` only has a `data/` stub —
      filter forward-return evaluation and full-wheel simulation
      using synthetic Black–Scholes pricing (per the point-in-time
      rules in `06-backtesting.md`).
- [ ] **05 — Web UI (remaining pages).** `/configs`, `/positions`,
      `/alerts`, `/backtest`, `/settings`, plus mutations beyond the
      watchlist and auth.

See [`CLAUDE.md`](CLAUDE.md) for conventions, the schema contract, and
how to add a filter or a new ingested source.

## Stack

- **Backend:** Python 3.11+, FastAPI, SQLAlchemy 2.x, Alembic, alpaca-py, `ta`
- **Storage:** SQLite (Postgres-compatible via SQLAlchemy)
- **Frontend:** React + Vite + Tailwind + shadcn/ui + TanStack Query
- **Tests:** pytest, pytest-asyncio
- **Lint/format:** ruff; **types:** mypy strict; **logs:** structlog

## Quick start

**Docker (recommended — works on Mac, Windows, Linux):**

```bash
cp .env.example .env       # fill in Alpaca keys
make dev                   # builds + starts backend :8000 + frontend :5173
# In another terminal:
docker compose exec backend python -m scripts.seed_dev
make docker-ingest-full    # backfill 5y of bars + indicators
make docker-test           # run the test suite
```

**Local (Mac / Linux with Python 3.11+ and Node 20+):**

```bash
cp .env.example .env       # fill in Alpaca keys
make install               # backend + frontend deps
make migrate               # create SQLite schema
make ingest-full           # backfill 5y of bars + indicators (paper data ok)
make run-backend           # FastAPI on :8000
make run-frontend          # Vite on :5173
```

> **Windows note:** Use the Docker path. The `py_vollib` dependency requires
> CPython internals not available in the Windows Store Python build.

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
