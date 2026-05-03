# 05 — Web UI

React + Vite + Tailwind frontend. FastAPI backend exposes a REST API. Single-user, runs locally or on a personal VPS.

## Pages

### `/` — Dashboard
Top-of-screen summary, refreshed on load:
- Today's screener hits (top 10 by score, grouped by config)
- Open positions with P&L, DTE, % max profit
- Macro context strip (VIX, term structure, SPY regime)
- Active alerts in last 24h
- Quick links to morning/evening digest content

### `/screener` — Screener detail
- Filter config selector (dropdown of saved configs)
- Date selector (replay any past day's screener output)
- Table of all candidates: symbol, score, close, distance from 200 EMA, RSI, IVP, target strike, target DTE, premium, annualized return
- Per-row expand → show every filter's pass/fail value (the `filter_results_json` from doc 02)
- Per-row "View chart" → modal with price chart + EMA overlays + IV history
- Per-row "Open trade" → opens position entry form pre-filled

### `/configs` — Filter config editor
- List of configs with active/inactive toggle
- Editor: visual form for each filter, threshold sliders, required-vs-scored toggle
- Save as new config / clone / delete
- "Test on today's data" → preview output without persisting
- "Backtest this config" → links to `/backtest` pre-loaded

### `/positions` — Position management
- Open positions table with live snapshot data
- Closed positions with P&L and cycle attribution
- Add position form (manual entry)
- Position detail page: leg history, snapshots over time as chart, management alerts fired

### `/tickers` — Watchlist
- Master ticker list with tier assignment
- Add/remove, set tier 1/2/3, notes
- Bulk import from CSV
- Per-ticker page: full chart, IV history, earnings dates, recent screener hits

### `/alerts` — Alert history (shipped)
- Chronological feed of alerts fired (newest first, paginated)
- Filter by type, symbol, since/until date range
- Click row → payload dialog (the JSON the Telegram template was
  rendered from)
- Per-row ack toggle (`user_acked` boolean on the `alerts` row;
  no audit table)

### `/backtest` — Backtesting (covered in doc 06)

### `/settings` — shipped
- Alert preferences per type (channels, quiet hours) — backed by
  `GET/PUT /api/alerts/preferences`.
- Channel status — `GET /api/system/channels` reports whether
  `TELEGRAM_BOT_TOKEN` is configured.
- Data refresh: last-run-at per registered job (from `GET /api/system/jobs`)
  plus a "Run ingestion now" button that posts to
  `/api/system/jobs/evening_pipeline/run`.

## Component library / design

- **Tailwind + shadcn/ui** for fast quality components
- **TanStack Query** for server state — handles caching and refetch on focus naturally
- **Zustand** for any UI-only state (filters open/closed, etc.)
- **Recharts** for most charts; **Lightweight Charts** (TradingView's library) for full price + indicator charts on ticker pages

## API contract (FastAPI routes)

```
# Screener
GET  /api/screener/results?date=YYYY-MM-DD&config_id=N
GET  /api/screener/results/{date}/{symbol}/{config_id}    # detail
POST /api/screener/run                                     # manual trigger

# Configs
GET    /api/configs
POST   /api/configs
GET    /api/configs/{id}
PUT    /api/configs/{id}
DELETE /api/configs/{id}
POST   /api/configs/{id}/test                              # dry-run on today

# Positions
GET    /api/positions?state=open
POST   /api/positions
PUT    /api/positions/{id}/transition                      # state change
GET    /api/positions/{id}/snapshots
GET    /api/positions/attribution

# Tickers
GET    /api/tickers
POST   /api/tickers
PUT    /api/tickers/{symbol}
DELETE /api/tickers/{symbol}
GET    /api/tickers/{symbol}/chart?range=1y                # bars + indicators
GET    /api/tickers/{symbol}/iv-history?range=1y
GET    /api/tickers/{symbol}/options                       # current chain

# Alerts
GET    /api/alerts?since=ISO8601
POST   /api/alerts/{id}/ack
GET    /api/alerts/preferences
PUT    /api/alerts/preferences

# Macro
GET    /api/macro/current
GET    /api/macro/history?range=6m

# System
GET    /api/system/status                                  # last refresh times, errors
POST   /api/system/refresh                                 # trigger ingestion
```

## Charts to prioritize

1. **Per-ticker price chart** with 20/50/200 EMA overlays, BB bands toggle, RSI panel below — main view for evaluating a candidate
2. **IV history chart** per ticker (line of IV30, with shaded historical range showing IVR/IVP context)
3. **Position P&L over time** from snapshots, with strike line and underlying overlaid
4. **Screener heatmap** — calendar view of how many candidates passed per day, color-coded by avg score (good for spotting market regime patterns)
5. **Macro strip on dashboard** — small VIX line, term structure indicator, SPY regime light

## Mobile responsive

You'll check this on your phone often. Optimize the dashboard and alert history for mobile first; backtesting and config editing can be desktop-primary.

## Auth

Single-user, local. For v1: HTTP basic auth via FastAPI dependency, credentials in env vars. If exposed publicly later, swap to OAuth proxy (Authelia, Caddy + Authentik).

## Module layout

```
backend/
  api/
    screener.py
    configs.py
    positions.py
    tickers.py
    alerts.py
    macro.py
    system.py
  main.py                  # FastAPI app, dependency injection

frontend/
  src/
    pages/
      Dashboard.tsx
      Screener.tsx
      Configs.tsx
      Positions.tsx
      Tickers.tsx
      Alerts.tsx
      Backtest.tsx
      Settings.tsx
    components/
      charts/
      tables/
      forms/
    api/                   # TanStack Query hooks per resource
    stores/
```
