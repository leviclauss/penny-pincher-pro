# Wheel Screener — Project Overview

A personal stock screener and alert system for running the wheel strategy (cash-secured puts → covered calls on assignment). Alert-only for now; no automated execution.

## Goals

- Surface high-quality wheel candidates daily based on configurable filters
- Track open positions through their lifecycle and alert on management triggers
- Provide a web UI to review screener output, adjust filters, and inspect history
- Backtest filter combinations against historical data via Alpaca

## Non-goals (for v1)

- Automated trade execution
- Multi-leg strategies (spreads, condors, strangles) — wheel only
- Multi-user / auth — single-user local deployment
- Mobile-native app — responsive web is enough

## Architecture at a glance

```
┌─────────────────┐    ┌──────────────────┐    ┌────────────────┐
│  Data Ingestion │───▶│  Storage (SQLite)│◀──▶│  FastAPI       │
│  - Alpaca bars  │    │  - bars          │    │  Backend       │
│  - Alpaca opts  │    │  - options       │    │                │
│  - Earnings     │    │  - signals       │    │                │
│  - VIX/macro    │    │  - positions     │    │                │
└─────────────────┘    │  - alerts        │    └────────┬───────┘
        ▲              │  - filter_configs│             │
        │              └──────────────────┘             ▼
┌───────┴──────────┐                              ┌─────────────┐
│ Scheduler        │                              │ Web UI      │
│ - Pre-market job │                              │ (React +    │
│ - Post-close job │                              │  Tailwind)  │
│ - Intraday alert │                              └─────────────┘
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ Notifier         │
│ - Email / Push   │
│ - Daily digest   │
└──────────────────┘
```

## Tech stack (recommended)

- **Backend:** Python 3.11+, FastAPI, SQLAlchemy
- **Storage:** SQLite for v1 (Postgres if it grows; both supported via SQLAlchemy)
- **Scheduling:** APScheduler embedded, or system cron — APScheduler keeps everything in one process
- **Market data:** `alpaca-py` SDK
- **Earnings:** Finnhub free tier or yfinance as fallback
- **Frontend:** React + Vite + TailwindCSS, TanStack Query for data fetching, Recharts or Lightweight Charts for visualization
- **Notifications:** SMTP for email; ntfy.sh or Pushover for mobile push (cheap and easy)
- **Deployment:** Single Docker Compose stack on a VPS or home server

## Implementation order

The docs below are numbered in the order I'd build them. Each piece is usable on its own — don't try to build the whole thing before deploying.

1. `01-data-ingestion.md` — Get bars, IV, earnings flowing into a database
2. `02-screener-filters.md` — Define and implement the filter pipeline
3. `03-alert-engine.md` — Trigger logic, dedup, and notification routing
4. `04-position-tracking.md` — Track open wheel positions and management alerts
5. `05-web-ui.md` — Frontend pages and API contract
6. `06-backtesting.md` — Historical evaluation of filters and full-wheel simulation
7. `07-scheduler-and-jobs.md` — Cron-style jobs for morning/evening summaries
8. `08-deployment.md` — Local dev, secrets, Docker, monitoring

## Files

- `00-overview.md` — this file
- `01-data-ingestion.md`
- `02-screener-filters.md`
- `03-alert-engine.md`
- `04-position-tracking.md`
- `05-web-ui.md`
- `06-backtesting.md`
- `07-scheduler-and-jobs.md`
- `08-deployment.md`
- `09-telegram-integration.md`
