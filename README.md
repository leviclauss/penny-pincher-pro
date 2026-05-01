# Penny Pincher Pro

A personal stock screener and alert system for the wheel options strategy
(cash-secured puts → covered calls). Alert-only — no automated execution.

## Status

Week 1: data ingestion (daily bars + indicators) and project skeleton.
See [`docs/planning/`](docs/planning/) for the full design spec, and
[`CLAUDE.md`](CLAUDE.md) for conventions and module layout.

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
