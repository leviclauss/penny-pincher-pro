# 04 — Position Tracking

Manual entry of trades (since no auto-execution). Tracks the wheel lifecycle from short put → assignment → covered call → close. Drives management alerts and historical performance.

## Wheel state machine

```
                  ┌──────────────────┐
                  │  No position     │
                  └────────┬─────────┘
                           │ sell CSP
                           ▼
            ┌──────────────────────────┐
            │  Short Put (open)        │
            └────┬───────┬───────┬─────┘
                 │       │       │
       expires  │  bought back  │  assigned
       worthless│  (profit/loss)│
                 ▼       ▼       ▼
         ┌──────────┐ ┌──────────┐ ┌────────────────┐
         │ Closed   │ │ Closed   │ │ Long 100 shares│
         │ (win)    │ │ (manual) │ └───────┬────────┘
         └──────────┘ └──────────┘         │ sell CC
                                            ▼
                                  ┌─────────────────────┐
                                  │ Covered Call (open) │
                                  └────┬───────┬────────┘
                                       │       │
                              expires  │  called away
                              worthless│  (assigned)
                                       ▼       ▼
                              ┌──────────┐ ┌──────────┐
                              │ Long 100 │ │ Closed   │
                              │ shares   │ │ (full    │
                              │ (sell    │ │  cycle)  │
                              │ another  │ └──────────┘
                              │  CC)     │
                              └──────────┘
```

## Schema

```sql
positions (
  id INTEGER PRIMARY KEY,
  symbol TEXT,
  state TEXT,                  -- 'short_put', 'long_shares', 'covered_call', 'closed'
  cycle_id INTEGER,            -- groups all legs of one wheel cycle
  opened_at DATETIME,
  closed_at DATETIME,
  notes TEXT
)

position_legs (
  id INTEGER PRIMARY KEY,
  position_id INTEGER,
  leg_type TEXT,               -- 'short_put', 'covered_call', 'shares'
  symbol TEXT,
  expiration DATE,             -- null for shares
  strike REAL,                 -- null for shares
  contracts INTEGER,           -- positive even though short — sign tracked in P&L
  shares INTEGER,              -- for shares legs
  entry_price REAL,            -- per-share for options ($/contract = price * 100)
  exit_price REAL,
  entry_date DATE,
  exit_date DATE,
  outcome TEXT,                -- 'expired', 'closed', 'assigned', 'called_away', 'open'
  realized_pnl REAL,
  fees REAL DEFAULT 0
)

position_snapshots (
  position_id INTEGER,
  snapshot_at DATETIME,
  underlying_price REAL,
  option_mid REAL,
  unrealized_pnl REAL,
  pct_max_profit REAL,         -- (entry - current) / entry for shorts
  delta REAL,
  dte INTEGER,
  PRIMARY KEY (position_id, snapshot_at)
)
```

## Manual entry UI

Form fields when adding a new short put:
- Symbol
- Expiration
- Strike
- Contracts
- Credit received (per contract)
- Date opened
- Optional: link to screener_results row that prompted the trade (for performance attribution)

When transitioning state:
- **Bought back / closed:** prompt for debit paid + date
- **Expired worthless:** one-click action, sets exit_price = 0
- **Assigned:** auto-creates `long_shares` leg at strike price, sets short_put outcome
- **Called away:** auto-closes shares at strike, marks call assigned

## Daily snapshot job

Each evening, for every open leg:
1. Fetch current underlying + option mid
2. Compute unrealized P&L, % max profit, current delta, DTE
3. Insert row into `position_snapshots`
4. Evaluate management rules → enqueue alerts if triggered

## Management rules (all configurable)

| Rule | Default | Alert type |
|---|---|---|
| 50% max profit on short put | 50% | "consider closing" |
| 21 DTE on short put | 21 days | "tastytrade rule" |
| Short put delta exceeds | 0.45 | "tested — strike under threat" |
| Underlying within X% of strike | 2% | "approaching breach" |
| Covered call ITM at 7 DTE | 7 days | "roll or let assign" |
| Position open longer than | 60 days | "stale — review" |

## Performance attribution

Each closed cycle computes:
- Total premium collected (puts + calls across the cycle)
- Cost basis if assigned (strike - cumulative put premium)
- Final exit price (called away strike or current mark if still holding)
- Days in cycle
- Annualized return on capital tied up
- Linked back to which `filter_config` originally surfaced the trade

This lets the UI show "which screener config has generated the best wheel cycles" — a far more useful metric than per-trade win rate.

## Module layout

```
positions/
  __init__.py
  models.py              # SQLAlchemy models
  state_machine.py       # Transitions, validation
  snapshot.py            # Daily snapshot job
  management.py          # Rule evaluation
  attribution.py         # Performance reporting
  api.py                 # FastAPI routes for CRUD
```

## Future: broker sync

When you eventually flip the auto-execute switch, the manual entry UI stays — you just gain a "sync from Alpaca" button that pulls open positions and reconciles with the local state. Designing the schema as broker-agnostic now (positions exist independent of Alpaca order IDs) makes that transition trivial.
