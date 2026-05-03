# 03 — Alert Engine

Turns screener results and intraday data into actionable notifications. Critical design constraint: **never spam yourself.** A noisy alert system gets muted; a muted alert system is useless.

## Implementation status

Phased delivery so each PR is independently useful:

- **Phase 1 — Daily digests (shipped).** Morning + evening digest builders in
  [`backend/alerts/triggers/digest.py`](../../backend/alerts/triggers/digest.py),
  Telegram templates, and scheduled
  [`morning_digest`](../../backend/scheduler/jobs/digest.py) /
  [`evening_digest`](../../backend/scheduler/jobs/digest.py) jobs. Each job
  short-circuits on NYSE holidays, when the latest bar is too stale (default
  4-day tolerance to cover long weekends), and when an alert for the same
  ``as_of`` date was already dispatched (dedup via ``payload_json.as_of``).
- **Phase 2 — Position-management dedup (shipped).**
  The ``position_management`` scheduler job evaluates the rules and
  dispatches one alert per trigger. ``fire_triggers`` now consults
  ``alerts.triggers._dedup.already_dispatched_for_position_rule`` before
  dispatching, enforcing the "max 1 per condition per position lifecycle"
  rule below. Re-runs (manual + scheduled on the same day, or daily
  evaluations of a stuck condition) are fully suppressed and reported
  via ``job_runs.result.alerts_suppressed``.
- **Phase 3 — Setup-triggered + IV-spike (shipped).** A new
  [`intraday_pulse`](../../backend/scheduler/jobs/intraday.py) job runs
  every ``SCHEDULER_INTRADAY_INTERVAL_MINUTES`` minutes (default 15)
  during RTH, NYSE-holiday-skipped, gated by quote freshness
  (``INTRADAY_QUOTE_MAX_AGE_S``, default 90 s). Disabled by default — opt
  in per deployment via ``SCHEDULER_INTRADAY_ENABLED=true``.
  - **Setup pass**: synthesizes an intraday ``FilterContext`` per symbol
    (today's bar overridden with the live mid; RSI(14) recomputed; EMAs
    and IV-derived indicators kept frozen because they barely move
    intraday), runs every active screener config, fires
    ``setup_triggered`` for the best-scoring hit per symbol. Suppressed
    if the symbol is in this morning's digest's ``screener_hits`` (via
    ``alerts.triggers._dedup.symbol_in_morning_digest``) or if a
    ``setup_triggered`` already fired for the symbol today.
  - **IV-spike pass** (off by default — pulls option chains and burns
    Alpaca quota; enable with ``INTRADAY_IV_SPIKE_ENABLED=true``):
    compares current ATM IV (computed via
    ``ingestion.iv.compute_atm_iv``) to the most recent stored
    ``indicators_daily.iv_atm``. Fires ``iv_spike`` when the percent
    change crosses ``INTRADAY_IV_SPIKE_PCT`` (default 0.20). Per-symbol
    chain pulls throttled to
    ``INTRADAY_IV_SPIKE_INTERVAL_MINUTES`` (default 30) so back-to-back
    pulse ticks don't re-pull. Same per-(symbol, day) dedup as setup.

  Both trigger families dedup via the shared
  ``already_dispatched_for_symbol_on(session, alert_type, as_of, symbol)``
  helper — payloads always carry ``as_of`` (ISO date) and ``symbol``.

The dispatcher (``alerts/dispatcher.py``), Telegram channel
(``alerts/channels/telegram.py``), and template renderer
(``alerts/templates/telegram_render.py``) are all in place and shared by
every trigger family.

The in-app history feed (``GET /api/alerts``, with optional ``since`` /
``until`` / ``alert_type`` / ``symbol`` filters and a ``user_acked``
toggle via ``POST /api/alerts/{id}/ack``) backs the ``/alerts`` page
in the web UI; every dispatched row shows up there regardless of which
channels actually delivered.

## Alert types

### Daily digest alerts (push at scheduled time)

- **Morning summary (e.g., 8:00 AM PT):**
  - Tickers passing screener overnight
  - Earnings today / this week on watchlist
  - Open positions with management triggers (50% profit, 21 DTE, ITM)
  - Macro context: VIX level, SPY regime, term structure
  - Overnight gappers on watchlist (>3% gap)

- **Evening summary (e.g., 5:00 PM PT after close):**
  - Today's screener hits with full ranked list
  - Position P&L summary
  - Tomorrow's earnings on watchlist
  - Fresh candidates added to consideration

### Real-time alerts (intraday)

These fire when conditions cross a threshold *during* RTH and need to be deduped aggressively:

- **Setup triggered:** ticker that wasn't in this morning's list now passes (e.g., midday selloff drops ABC into 200 EMA range)
- **Position breach:** short put strike breached, or stock drops to within X% of strike
- **IV spike:** IV jumps >20% in a session (often = opportunity)
- **Earnings surprise:** unscheduled news halt or 8-K filing

### Position management alerts (event-driven)

- 50% of max profit captured → consider closing
- 21 DTE reached on open short put → tastytrade rule
- Short put assigned → "wheel turned, time to sell call" reminder
- Covered call ITM near expiry → roll or let assign decision

## Dedup and rate limiting

A single ticker can trigger many filters at once. Group alerts so one notification covers all reasons:

```
[Setup] AAPL — Conservative Wheel + Aggressive Oversold
  Close: $172.40 (1.2% above 200 EMA $170.36)
  RSI: 32 | IVP: 67 | No earnings in 38d
  Target: 30 Apr $170 put @ ~$2.10 (5.4% ann. return)
```

Dedup rules:
- Same ticker + same config → max 1 alert per day
- Position alerts → max 1 per condition per position lifecycle
- Intraday "setup triggered" → suppressed if ticker already in morning summary

## Notification channels

Build channel-agnostic — alerts produce a structured payload, channel adapters serialize it:

- **Email (SMTP):** reliable, good for digests with rich formatting (HTML)
- **Push (ntfy.sh or Pushover):** quick on phone, good for intraday
- **Webhook:** future hook for Discord/Slack/iOS Shortcuts
- **In-app:** UI shows alert history regardless of channel

User config per alert type → which channels:

```json
{
  "morning_digest": ["email"],
  "evening_digest": ["email"],
  "setup_triggered": ["push"],
  "position_breach": ["push", "email"],
  "iv_spike": ["push"]
}
```

## Schema

```sql
alerts (
  id INTEGER PRIMARY KEY,
  alert_type TEXT,             -- 'morning_digest', 'setup_triggered', etc.
  symbol TEXT,                 -- nullable (digests cover many)
  config_id INTEGER,           -- nullable
  payload_json TEXT,           -- the structured alert
  triggered_at DATETIME,
  channels_sent TEXT,          -- comma-separated channels delivered to
  user_acked BOOLEAN DEFAULT FALSE
)

alert_preferences (
  alert_type TEXT PRIMARY KEY,
  channels TEXT,               -- JSON array
  enabled BOOLEAN,
  quiet_hours_start TIME,      -- e.g., 22:00 PT
  quiet_hours_end TIME         -- e.g., 06:00 PT
)
```

## Module layout

```
alerts/
  __init__.py
  triggers/
    digest.py          # Morning/evening digest builders
    setup.py           # New-candidate detection vs prior run
    position.py        # Position management rules
    intraday.py        # Real-time IV/price triggers
  channels/
    base.py            # Channel ABC
    email.py
    push.py            # ntfy.sh adapter
    webhook.py
  dispatcher.py        # Dedup, rate limit, channel routing
  templates/           # HTML email + push templates
    morning_digest.html
    evening_digest.html
    setup.html
    position.html
```

## Quiet hours and weekends

Push alerts respect quiet hours. Position breach alerts can override quiet hours optionally. Weekends → only urgent class alerts (none for wheel screener; markets closed). Earnings on watchlist for Monday morning fires Sunday evening as a weekend digest.

## Failure modes to handle

- SMTP failure → fall back to push, log to alert history with `channels_sent` empty for the failed channel
- Stale data: if last bar in DB is >24h old during RTH, don't fire intraday alerts (something's broken)
- Earnings data missing for a ticker → tag alert with ⚠️ warning rather than silently passing the no-earnings filter
