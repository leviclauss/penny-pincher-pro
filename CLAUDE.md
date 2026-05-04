# CLAUDE.md

Conventions, architecture, and "how to add a thing" for Claude (and any other
contributor) working on this repo. The authoritative product spec is
[`docs/planning/`](docs/planning/) (00–08); this file is the engineering
contract layered on top of it.

## Project at a glance

Personal stock screener and alert system for the wheel options strategy
(cash-secured puts → covered calls). Alert-only — no automated execution.
Single-user, runs locally or on a small VPS.

Two tracks:

- **Platform / data / infra** — `backend/ingestion/`, `backend/api/`,
  `backend/db/`, `backend/scheduler/`, `backend/core/`. Owns the schema,
  data pipelines, FastAPI app, jobs, deployment.
- **Filters / strategy / backtesting** — `backend/screener/`,
  `backend/backtest/`. Owns the filter pipeline, scoring, and historical
  evaluation. Reads from the schema produced by the platform track.

## Stack (locked)

- Python 3.11+, FastAPI, SQLAlchemy 2.x (Mapped[] / select() style),
  Alembic, Pydantic v2 + pydantic-settings, alpaca-py, `ta` (indicators),
  `py_vollib` (Black-Scholes IV inversion fallback), `httpx` (Finnhub +
  Yahoo Finance HTTP), `APScheduler` (embedded scheduler),
  `pandas_market_calendars` (NYSE holiday awareness), pandas, structlog,
  tenacity, click.
- pytest + pytest-asyncio + syrupy (snapshots).
- ruff (lint + format), mypy strict.
- SQLite for v1 (Postgres-ready via SQLAlchemy).
- Frontend: Vite + React + TypeScript + Tailwind v3 + shadcn/ui-compatible
  tokens + TanStack Query + react-router-dom + recharts.

## Repo layout

```
backend/
  api/          FastAPI routers, one file per resource; main.py wires them
  core/         config, logging, time — cross-cutting
  db/           session.py + models/ (one file per domain)
  ingestion/    *_client.py wrappers + per-source fetchers + pipeline.py CLI
  screener/     filters/, registry.py, pipeline.py  (PARTNER TRACK)
  scheduler/    app.py (factory + JOB_REGISTRY), context.py (job_run), jobs/
  alerts/       dispatcher.py, channels/, templates/, triggers/
  positions/    Wheel lifecycle + management rules
  backtest/     pricing.py / portfolio.py / simulator.py + cli.py
  alembic/      Migrations
  scripts/      One-off scripts (seed_dev.py, etc.)
  tests/        Mirrors code paths; fixtures in tests/fixtures/
frontend/src/   api/, lib/, pages/, components/ (AppShell + ui/ + charts/)
docs/planning/  Product spec (00-overview … 08-deployment)
```

## The schema contract

The full schema is defined in `backend/db/models/` and frozen by the
initial Alembic migration. All 17 tables exist from day one even though
many are populated by future sessions — this is the contract between the
two tracks. **Don't rename or change column types without notifying the
other track.**

| Module | Tables | Owned by |
|---|---|---|
| `db.models.market` | `tickers`, `bars_daily`, `indicators_daily`, `options_snapshot`, `options_historical`, `earnings`, `macro_daily` | platform (writer), screener (reader) |
| `db.models.screener` | `filter_configs`, `screener_results` | screener |
| `db.models.alerts` | `alerts`, `alert_preferences` | platform |
| `db.models.positions` | `positions`, `position_legs`, `position_snapshots` | platform |
| `db.models.backtest` | `backtest_runs`, `backtest_trades`, `backtest_equity` | screener |
| `db.models.system` | `job_runs` | platform |

Schema decisions worth knowing:

- All datetimes are `DateTime(timezone=True)`, stored as UTC. Use
  `core.time.utcnow()` for defaults.
- `indicators_daily.ema_200_weekly` is nullable — needs ~200 weeks of
  history before it's meaningful.
- `indicators_daily.iv_atm` is populated by the options pass when an
  option chain exists for the symbol on the as-of date.
- `indicators_daily.iv_rank` / `iv_percentile` need a 252-day rolling
  window; they remain NULL until ≥126 days of valid `iv_atm` history
  accumulate. The 126-day warm-up can be skipped by backfilling
  `iv_atm` from `options_historical` via `python -m ingestion.iv_backfill`.
- `options_snapshot.volume` and `open_interest` stay NULL on the
  free Alpaca tier; switch `OPTIONS_PROVIDER=polygon` (with
  `POLYGON_API_KEY`) to populate them.
- `options_snapshot` is a current-only table — each ingestion run
  replaces the symbol's prior rows so stale strikes don't linger after
  the underlying moves.
- `options_historical` accumulates daily per-contract OHLCV (backfilled
  from Polygon via `python -m ingestion.options_history --start ... --end ...`).
  Powers the strategy backtest's `RealChainPricer` mode (CLI flag
  `--use-real-chain`) and the `iv_backfill` pass that seeds historical
  `iv_atm`. Polygon Developer doesn't expose historical bid/ask at this
  tier, so `close` is the stored mark.
- `earnings` is populated by Finnhub (free tier, US equities only) for
  the next ~90 days; without `FINNHUB_API_KEY` the earnings step
  silently no-ops rather than failing the whole pipeline.
- `tickers.sector` / `market_cap` are populated by a separate one-shot
  flow (`python -m ingestion.ticker_metadata`), not the daily pipeline.
  Sector is sourced from Finnhub's `finnhubIndustry`; ETFs (SPY, QQQ)
  have no profile on the free tier and stay NULL.
- `macro_daily.spy_ema_200` is read from `indicators_daily` (single
  source of truth); the macro fetcher must run after the indicator step.
- `macro_daily.vix_term_structure` = `vix_9d / vix_close`; values < 1
  indicate backwardation per doc 01.
- `job_runs` is written by every scheduled or manually-triggered job
  via the ``scheduler.context.job_run`` context manager — always one
  row per execution, including failures (status=failure + error).
- JSON columns use SQLAlchemy `JSON` (TEXT on SQLite, JSONB on Postgres).
- Foreign keys cascade on positions/backtest child tables.
- Composite primary keys for time-series rows (`(symbol, date)`,
  `(run_id, date)`, etc.); single-column PKs everywhere else.

## Conventions

- **Type hints everywhere.** mypy `--strict` runs in CI.
- **Ruff** for lint + format. Don't pre-commit-format other people's code
  in unrelated PRs; only the files you actually touched.
- **Pydantic v2** for API request/response shapes. Pure dataclasses for
  internal value objects (e.g., `BarRecord`).
- **SQLAlchemy 2.0 style only:** `Mapped[T]`, `mapped_column(...)`,
  `select(...)`, `session.execute(...).scalar_one()`. No legacy `query()`.
- **Async where it matters** (FastAPI handlers, HTTP I/O), **sync where
  simpler** (CLI, indicator math, ingestion). Don't async for its own sake.
- **One logger per module:** `log = get_logger(__name__)` from
  `core.logging`. Use structured fields, not f-strings:
  `log.info("bars.fetch.done", symbols=n, bars=k)`.
- **Tests live in `tests/`** mirroring code paths. Fixtures in
  `tests/fixtures/`. Use syrupy for snapshot tests of computed series.
- **Secrets** come from env vars / `.env` (never committed).
  `.env.example` lists every required variable.
- **Comments**: only when WHY isn't obvious. Don't restate WHAT the code
  does. No "added for X" or "used by Y" — that rots.
- **Conventional commits**: `feat(scope): …`, `fix(scope): …`,
  `chore(scope): …`, `docs: …`. Scopes match top-level dirs (`db`,
  `ingestion`, `api`, `frontend`, etc.).

## Common tasks

Standard targets (`install`, `test`, `lint`, `format`, `typecheck`,
`migration m="…"`, `migrate`, `db-reset`, `ingest-full`,
`ingest-incremental`, `run-backend`, `run-frontend`) live in the
`Makefile` — read it for the full list.

Non-obvious commands:

| Task | Command |
|---|---|
| Seed dev watchlist | `cd backend && python -m scripts.seed_dev` |
| Refresh ticker metadata (sector, market_cap) | `cd backend && python -m ingestion.ticker_metadata` |
| Backfill historical option chains (Polygon) | `cd backend && python -m ingestion.options_history --start YYYY-MM-DD --end YYYY-MM-DD` |
| Backfill IV from options_historical | `cd backend && python -m ingestion.iv_backfill --start YYYY-MM-DD --end YYYY-MM-DD` |
| Bars-only ingestion (skip slow steps) | `python -m ingestion.pipeline --incremental --skip-options --skip-earnings --skip-macro` |
| Backend without scheduler | `SCHEDULER_ENABLED=false make run-backend` |
| Trigger any job manually | `curl -X POST http://localhost:8000/api/system/jobs/{job_id}/run` |
| Intraday pulse (only when `SCHEDULER_INTRADAY_ENABLED=true`) | `curl -X POST http://localhost:8000/api/system/jobs/intraday_pulse/run` |
| Update indicator snapshot | `cd backend && pytest tests/test_indicators.py --snapshot-update` |
| Filter backtest | `cd backend && python -m backtest.cli --mode filter --config-id N --start YYYY-MM-DD --end YYYY-MM-DD` |
| Strategy backtest | `cd backend && python -m backtest.cli --mode strategy --config-id N --start YYYY-MM-DD --end YYYY-MM-DD --starting-capital 10000` |
| Strategy backtest with real chain prices | append `--use-real-chain` (requires `options_history` backfill) |

Strategy-backtest knobs (all optional, defaults in
`backtest/cli.py`): `--max-concurrent-positions`, `--delta-target`,
`--dte-target`, `--profit-take-pct`, `--manage-dte`, `--symbols
AAPL,MSFT`. Filter mode takes `--forward-days` for the exit-window
length.

First run from a clean clone:

```bash
cp .env.example .env       # add Alpaca keys
make install
make migrate
cd backend && python -m scripts.seed_dev
make ingest-full           # ~5 years of bars + indicators
make run-backend
```

## API + Web UI

Both surfaces decay fast — read the source rather than this file:

- **API routes:** `backend/api/main.py` wires the routers; one file per
  resource under `backend/api/`. Read-only, no auth. Range tokens on
  chart/IV/macro endpoints: `1m`, `3m`, `6m`, `1y`, `2y`, `5y`, `max`.
- **Frontend pages:** `frontend/src/pages/` (Dashboard, Tickers,
  TickerDetail, Alerts, Backtest, etc.). Fetches go through TanStack
  Query helpers in `src/api/client.ts` with shapes in `src/api/types.ts`.
  shadcn-shaped primitives live directly in `src/components/ui/` — no
  shadcn CLI; design tokens are in `src/index.css`.

Out of scope until later sessions: `/configs`, `/settings`, auth,
write mutations beyond the alert-ack toggle.

## How to add a screener filter (partner track)

Every filter is a pure function: takes the day's data for one ticker
plus a config dict, returns a pass/fail + score.

1. **Define the filter class** in the appropriate module under
   `backend/screener/filters/` (e.g., `technical.py`). The base class
   and `FilterContext` / `FilterResult` shapes live in
   `backend/screener/filters/base.py`.
2. **Register it** in `backend/screener/registry.py` so config JSON
   strings (`"id": "near_200ema"`) resolve to your class.
3. **Document defaults** in [`docs/planning/02-screener-filters.md`](docs/planning/02-screener-filters.md).
4. **Add tests** in `backend/tests/test_<filter>.py`. Use the synthetic
   bars fixture (`tests/fixtures/bars.py`); snapshot-test numeric series.
5. The pipeline picks it up automatically the next time it loads configs.

### Point-in-time correctness (non-negotiable for backtesting)

Filters must use only data with `as_of` ≤ the evaluation date. Indicators
in the DB are already point-in-time per row. Options snapshots are
*current-only* — historical chains aren't stored, so backtests use
synthetic Black-Scholes pricing (see
[`docs/planning/06-backtesting.md`](docs/planning/06-backtesting.md)).

## How to add an alert trigger

Triggers are pure payload builders. The dispatcher
(``alerts.dispatcher.dispatch``) handles channel fan-out, persistence, and
quiet-hour gating — trigger code only decides *when* to fire and *what's
in the payload*.

1. **Pick the family** — daily summary (`triggers/digest.py`),
   position-management (`positions.management.fire_triggers`), intraday
   (`triggers/intraday.py`), etc.
2. **Build the payload as a dict** keyed exactly the way the matching
   Jinja template expects. Pick the matching dedup helper:
   - per-day events (digests, daily setup hits) → include an ``as_of``
     ISO date string and use
     `alerts.triggers._dedup.already_dispatched_for_as_of`.
   - per-position lifecycle events (management rules) → include
     ``position_id`` and ``rule`` and use
     `alerts.triggers._dedup.already_dispatched_for_position_rule`.
     Each new wheel cycle is a fresh ``Position`` row, so matching on
     ``position_id`` alone naturally resets the lifecycle dedup.
   - intraday per-symbol events (``setup_triggered``, ``iv_spike``) →
     include ``as_of`` + ``symbol`` and use
     `alerts.triggers._dedup.already_dispatched_for_symbol_on`. For
     ``setup_triggered`` also call
     `alerts.triggers._dedup.symbol_in_morning_digest` first to enforce
     the doc 03 rule "suppress if ticker already in morning summary."
3. **Add a Telegram template** under
   `backend/alerts/templates/telegram/<alert_type>.md.j2`. Run every
   payload value through the `esc` filter — MarkdownV2 will silently
   drop the message otherwise. Snapshot-test renders via syrupy
   (see `tests/test_telegram_render.py`).
4. **Wire a scheduler job** in `backend/scheduler/jobs/` that:
   1. holiday-skips via `pandas_market_calendars`,
   2. dedups via the helper from step 2,
   3. checks bar freshness via `alerts.triggers._freshness.check_bar_freshness`,
   4. calls the builder + `dispatcher_module.dispatch`,
   5. wraps everything in `scheduler.context.job_run()` so successes /
      skips / failures all land in `job_runs`.
5. **Register the job** in `scheduler/app.py` (`_register_*` + `register_job`)
   so it gets a cron + shows up in the scheduled-jobs UI.
6. **Tests:** unit tests for the builder against an in-memory SQLite DB
   (alembic-migrated like `test_alerts_dispatcher.py`), plus an
   end-to-end job test that asserts the holiday/stale/dedup branches
   each write the right `job_runs` row.

## How to add a new ingested data source (platform track)

1. Add a model file in `backend/db/models/<domain>.py` and re-export it
   in `backend/db/models/__init__.py`.
2. `make migration m="add <table>"`, review the generated migration,
   `make migrate`.
3. Add fetcher in `backend/ingestion/<source>.py`. Use a `*_client.py`
   wrapper — never call SDKs directly from the pipeline.
4. Wire it into `ingestion.pipeline` if it should be part of the daily run.
5. Add tests under `backend/tests/` using a `Fake*Client`-style stub.
   Don't hit the network in tests.

## CI

`.github/workflows/ci.yml` runs:

- **Backend:** `ruff check` + `ruff format --check` + `mypy --strict` + `pytest`.
- **Frontend:** `tsc -b --noEmit` + `vite build`.

Keep CI green. If a check fails on `main`, that's the next thing to fix.

## Out-of-scope tripwires

These are intentionally unbuilt and should not creep in without
discussion:

- Auto-execution of trades.
- Multi-leg option strategies (spreads, condors, strangles).
- Multi-user / auth beyond single-user basic auth.
- Real-time streaming (intraday alerts via polling are fine for v1).

## Source-of-truth links

- Product spec: [`docs/planning/`](docs/planning/) (00-overview through
  08-deployment).
- Schema: `backend/db/models/`.
- Backtest methodology: [`docs/planning/06-backtesting.md`](docs/planning/06-backtesting.md).
- Stack & deployment: [`docs/planning/08-deployment.md`](docs/planning/08-deployment.md).
- Production deploy runbook (Lightsail + Tailscale + compose): [`docs/deploy.md`](docs/deploy.md).
- Filter catalog: [`docs/planning/02-screener-filters.md`](docs/planning/02-screener-filters.md).
- External data-source map (provider → tables): [`docs/ops/data-sources.md`](docs/ops/data-sources.md).
