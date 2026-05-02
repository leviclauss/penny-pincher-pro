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
  Yahoo Finance HTTP), pandas, structlog, tenacity, click.
- pytest + pytest-asyncio + syrupy (snapshots).
- ruff (lint + format), mypy strict.
- SQLite for v1 (Postgres-ready via SQLAlchemy).
- Frontend: Vite + React + TypeScript + Tailwind v3 + shadcn/ui-compatible
  tokens + TanStack Query.

## Repo layout

```
backend/
  api/              FastAPI app, route modules (per resource)
                    main.py wires routers; one file per resource
                    (tickers.py, macro.py, earnings.py, …)
  core/             config, logging, time — cross-cutting
  db/
    session.py      Base, engine, sessionmaker, get_session() ctx mgr
    models/         ORM models grouped by domain (one file per domain)
  ingestion/
    alpaca_client.py    Bars SDK wrapper + retry
    options_client.py   Options SDK wrapper + retry + OCC parsing
    finnhub_client.py   Finnhub HTTP wrapper (earnings calendar)
    yahoo_client.py     Yahoo Finance HTTP wrapper (VIX/VIX9D index data)
    bars.py             Daily bars fetcher (full + incremental)
    options.py          Option chain fetcher (current snapshot)
    earnings.py         Earnings calendar fetcher (next 90 days)
    macro.py            VIX/SPY macro fetcher + regime derivation
    indicators.py       Technical indicators (pure functions)
    iv.py               ATM IV / rank / percentile + BS inversion
    persistence.py      DataFrame ↔ DB helpers
    pipeline.py         Orchestration + click CLI
  screener/         Filter pipeline + scoring  (PARTNER TRACK)
    filters/
      base.py             Filter ABC + FilterResult dataclass (TBD)
      technical.py | volatility.py | liquidity.py | event.py | economics.py
    pipeline.py     Loads config, runs filters, persists results
    registry.py     Maps filter ID strings to classes
  scheduler/        APScheduler jobs (later session)
  alerts/           Triggers + channel adapters (later session)
  positions/        Wheel lifecycle + management rules (later session)
  backtest/         Filter forward-return + full strategy sim (later)
  alembic/          Migrations
  scripts/          One-off scripts (seed_dev.py, etc.)
  tests/            Tests live alongside code; fixtures in tests/fixtures/

frontend/
  src/
    api/            client.ts (typed fetch helpers) + types.ts
    lib/            utils (cn) + format helpers
    pages/          Dashboard, Tickers, TickerDetail, NotFound
    components/     AppShell + ui/ (shadcn-shaped Card/Button/Table)
                    + charts/ (PriceChart, RsiChart, IvHistoryChart)

docs/planning/      Product spec (00-overview … 08-deployment)
data/               SQLite db (gitignored)
.github/workflows/  CI
```

## The schema contract

The full schema is defined in `backend/db/models/` and frozen by the
initial Alembic migration. All 17 tables exist from day one even though
many are populated by future sessions — this is the contract between the
two tracks. **Don't rename or change column types without notifying the
other track.**

| Module | Tables | Owned by |
|---|---|---|
| `db.models.market` | `tickers`, `bars_daily`, `indicators_daily`, `options_snapshot`, `earnings`, `macro_daily` | platform (writer), screener (reader) |
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
  accumulate (no backfill — Alpaca's options history is shallow).
- `options_snapshot.volume` and `open_interest` stay NULL on the
  free Alpaca tier (the snapshot endpoint doesn't expose them). Filters
  that depend on those columns require a paid feed (ORATS/CBOE).
- `options_snapshot` is a current-only table — each ingestion run
  replaces the symbol's prior rows so stale strikes don't linger after
  the underlying moves.
- `earnings` is populated by Finnhub (free tier, US equities only) for
  the next ~90 days; without `FINNHUB_API_KEY` the earnings step
  silently no-ops rather than failing the whole pipeline.
- `macro_daily.spy_ema_200` is read from `indicators_daily` (single
  source of truth); the macro fetcher must run after the indicator step.
- `macro_daily.vix_term_structure` = `vix_9d / vix_close`; values < 1
  indicate backwardation per doc 01.
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

| Task | Command |
|---|---|
| Install everything | `make install` |
| Run tests | `make test` (or `cd backend && pytest -q`) |
| Lint | `make lint` |
| Format | `make format` |
| Typecheck | `make typecheck` |
| New migration | `make migration m="describe change"` |
| Apply migrations | `make migrate` |
| Reset DB | `make db-reset` |
| Seed dev watchlist | `cd backend && python -m scripts.seed_dev` |
| Full ingestion | `make ingest-full` |
| Daily ingestion | `make ingest-incremental` |
| Skip options (fast bars-only) | `python -m ingestion.pipeline --incremental --skip-options` |
| Skip everything but bars | `python -m ingestion.pipeline --incremental --skip-options --skip-earnings --skip-macro` |
| Backend dev server | `make run-backend` |
| Frontend dev server | `make run-frontend` |
| Update indicator snapshot | `cd backend && pytest tests/test_indicators.py --snapshot-update` |

A typical first run from a clean clone:

```bash
cp .env.example .env       # add Alpaca keys
make install
make migrate
cd backend && python -m scripts.seed_dev
make ingest-full           # ~5 years of bars + indicators
make run-backend
```

## API surface (today)

Read-only, no auth. Routes are wired in `backend/api/main.py`; one
file per resource under `backend/api/`:

| Route | Source | Notes |
|---|---|---|
| `GET /api/system/health` | `api/main.py` | last bar date, bar count |
| `GET /api/tickers` | `api/tickers.py` | watchlist + latest close, EMA200, RSI, IV ATM, next earnings |
| `GET /api/tickers/{symbol}/chart?range=1y` | `api/tickers.py` | OHLCV joined with EMA20/50/200 + RSI |
| `GET /api/tickers/{symbol}/iv-history?range=1y` | `api/tickers.py` | iv_atm / iv_rank / iv_percentile series |
| `GET /api/macro/current` | `api/macro.py` | most recent macro_daily row, or null |
| `GET /api/macro/history?range=6m` | `api/macro.py` | VIX/SPY series |
| `GET /api/earnings/upcoming?days=7` | `api/earnings.py` | active-watchlist earnings within window |

Range tokens accepted by chart/IV/macro endpoints: `1m`, `3m`, `6m`,
`1y`, `2y`, `5y`, `max` (subset varies by endpoint).

## Web UI status

Routes shipped (read-only, mobile-responsive shell with sidebar nav):

- `/` — Dashboard: macro strip (VIX, VIX9D, term-structure pill,
  SPY-vs-200-EMA light), watchlist freshness, upcoming earnings (next
  7 days). "Recent ingestion runs" placeholder waits on `job_runs`.
- `/tickers` — Sortable watchlist table; row click → ticker detail.
- `/tickers/{symbol}` — Header (last close + day change), 1y price
  chart with toggleable EMA 20/50/200 overlays and earnings reference
  lines, RSI(14) sub-panel, IV ATM history.

Stack additions:
- `react-router-dom` v6 for routing.
- `recharts` for price/RSI/IV charts (Lightweight Charts can replace
  later if/when we want true candlesticks).
- shadcn-shaped primitives live directly in `src/components/ui/` —
  no shadcn CLI; design tokens are already in `src/index.css`.
- Every fetch goes through TanStack Query; helpers in
  `src/api/client.ts`, response shapes in `src/api/types.ts`.

Out of scope until later sessions: `/screener`, `/configs`,
`/positions`, `/alerts`, `/backtest`, `/settings`, auth, and any
mutations.

## How to add a screener filter (partner track)

The filter contract is intentionally minimal: every filter is a pure
function that takes the day's data for one ticker plus a config dict and
returns a pass/fail + score.

**1. Define the filter class** in the appropriate module under
`backend/screener/filters/` (e.g., `technical.py`, `volatility.py`).
The base class lives in `backend/screener/filters/base.py` (to be
authored when the screener pipeline is built; the shape will be):

```python
from dataclasses import dataclass
from typing import Any

@dataclass
class FilterContext:
    symbol: str
    as_of: date
    bars: pd.DataFrame           # all bars up to as_of (point-in-time)
    indicators: pd.Series        # latest row of indicators_daily
    options_chain: pd.DataFrame | None
    earnings: list[date]
    ticker: TickerRow

@dataclass
class FilterResult:
    passed: bool
    score: float | None          # 0–1, or None if not used in scoring
    value: float | str | None    # the value being thresholded (for UI)
    reason: str | None           # human-readable diagnostic

class Filter(Protocol):
    id: str
    def evaluate(self, ctx: FilterContext, params: dict[str, Any]) -> FilterResult: ...
```

**2. Register it** in `backend/screener/registry.py` so config JSON
strings (`"id": "near_200ema"`) resolve to your class.

**3. Document defaults** in [`docs/planning/02-screener-filters.md`](docs/planning/02-screener-filters.md)
if they aren't already there.

**4. Add tests** in `backend/tests/test_<filter>.py`. Use the existing
synthetic bars fixture (`tests/fixtures/bars.py`) for deterministic
inputs. Snapshot tests are appropriate where output is a numeric series.

**5. The pipeline picks it up automatically** the next time it loads
configs — no orchestration changes needed.

### Point-in-time correctness (non-negotiable for backtesting)

Filters must use only data with `as_of` ≤ the evaluation date. Indicators
in the DB are already point-in-time per row. Options snapshots are
*current-only* — historical chains aren't stored, so backtests use
synthetic Black-Scholes pricing (see
[`docs/planning/06-backtesting.md`](docs/planning/06-backtesting.md)).

## How to add a new ingested data source (platform track)

1. Add a model file in `backend/db/models/<domain>.py` and re-export it
   in `backend/db/models/__init__.py`.
2. `make migration m="add <table>"`, review the generated migration,
   `make migrate`.
3. Add fetcher in `backend/ingestion/<source>.py`. Use
   `ingestion.alpaca_client.AlpacaClient` (or a peer wrapper) — never
   call SDKs directly from the pipeline.
4. Wire it into `ingestion.pipeline` if it should be part of the daily run.
5. Add tests under `backend/tests/` using a `FakeAlpacaClient`-style
   stub. Don't hit the network in tests.

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
- Stack & deployment: [`docs/planning/08-deployment.md`](docs/planning/08-deployment.md).
- Filter catalog: [`docs/planning/02-screener-filters.md`](docs/planning/02-screener-filters.md).
