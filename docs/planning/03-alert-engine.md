# 03 — Alert Engine

Turns screener results and intraday data into actionable notifications. Critical design constraint: **never spam yourself.** A noisy alert system gets muted; a muted alert system is useless.

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
