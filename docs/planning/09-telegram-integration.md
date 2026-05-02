# 09 — Telegram Integration

Plan for adding Telegram as a notification channel (and, later, a thin
command surface). Written before the screener pipeline exists, so the
plan is split into phases that don't depend on screener output. Phase 1
is buildable today against the alert engine scaffolding in
[`03-alert-engine.md`](03-alert-engine.md).

## Why Telegram

- Free, no per-message cost (vs Pushover), no domain setup (vs SMTP), no
  daily message cap that matters at single-user volume.
- Supports rich formatting (MarkdownV2 / HTML), inline keyboards, file
  attachments — better than `ntfy.sh` for digests.
- Mobile + desktop + web clients out of the box.
- Optional inbound: a future `/status`, `/macro`, `/snooze` bot reuses
  the same token + chat without standing up a webhook server.

Trade-offs:
- A long-lived bot token is a secret; leaking it lets anyone DM the bot
  (but not the user — sending only works to chats the user opted into).
- Telegram rate-limits at ~30 messages/sec global, 1 msg/sec per chat —
  fine for digests, worth knowing for any future fan-out.

## Phasing

| Phase | Scope | Depends on |
|---|---|---|
| **1 — Outbound channel** | Bot token + chat id, `TelegramChannel` adapter, payload renderer, dispatcher wiring, `alert_preferences` accepts `"telegram"`, manual `/api/system/alerts/test` route to send a canned payload. | Nothing. Buildable now. |
| **2 — Digest rendering** | Morning / evening digest templates rendered as MarkdownV2; "Open in app" deep links to the local web UI (Tailscale URL). | Screener pipeline (doc 02) + digest builder (doc 03). |
| **3 — Inbound commands** | Long-poll bot for `/status`, `/macro`, `/positions`, `/snooze <ticker> <hours>`. Restrict to a single allow-listed chat id. | Phase 1 + read APIs already shipped. |
| **4 — Inline acks** | Inline keyboard buttons on alerts ("Ack", "Snooze 1h", "Open in UI") that mark `alerts.user_acked = TRUE` via the bot's callback handler. | Phase 3. |

Each phase is independently shippable. Phase 1 alone gives us a working
channel that anything in `alerts/` can target — the digest builders just
hand it a `payload_json` and Telegram is one of the recipients.

## Phase 1 — Outbound channel

### Bot setup (one-time, manual)

1. DM `@BotFather` on Telegram → `/newbot` → choose name + username →
   record the HTTP API token.
2. Start a chat with the new bot, send any message.
3. `curl https://api.telegram.org/bot<TOKEN>/getUpdates` → copy
   `result[0].message.chat.id` (a positive integer for DMs, negative for
   groups). This is the only chat the adapter will send to in v1.
4. Store both in `.env` (never committed).

### Configuration

Add to `backend/core/config.py`:

```python
telegram_bot_token: str = Field(default="")
telegram_chat_id: str = Field(default="")           # stringified int
telegram_parse_mode: str = "MarkdownV2"             # or "HTML"
telegram_disable_preview: bool = True
telegram_timeout_s: float = 10.0
```

Add to `.env.example` (with empty values + comments mirroring Finnhub's
"silently no-ops without a key" pattern):

```
# --- Telegram (optional notification channel) ---
# Create a bot via @BotFather and DM it once to seed getUpdates.
# Without TELEGRAM_BOT_TOKEN the channel is unregistered and any
# alert_preferences entry containing "telegram" is treated as a no-op
# (logged at WARNING).
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_PARSE_MODE=MarkdownV2
TELEGRAM_DISABLE_PREVIEW=true
```

The "no token → silent no-op" mirrors the Finnhub earnings behavior so a
fresh clone runs without hard-failing the alert dispatcher.

### Module layout

```
backend/alerts/channels/
  base.py              # Channel ABC + ChannelResult dataclass
  telegram.py          # TelegramChannel
  email.py             # (later)
  push.py              # (later)
backend/ingestion/      # unchanged — this is alert-side
backend/alerts/templates/
  telegram/
    morning_digest.md.j2
    evening_digest.md.j2
    setup.md.j2
    position.md.j2
```

`base.py` (sketch — to be authored alongside the first concrete channel,
matching the `Filter` Protocol pattern in CLAUDE.md):

```python
from dataclasses import dataclass
from typing import Any, Protocol

@dataclass
class ChannelResult:
    delivered: bool
    provider_message_id: str | None
    error: str | None

class Channel(Protocol):
    id: str                              # 'telegram', 'email', 'push'
    def send(self, alert_type: str, payload: dict[str, Any]) -> ChannelResult: ...
```

### TelegramChannel implementation

A thin `httpx`-based client (no `python-telegram-bot` dep yet — Phase 3
adds it for polling). HTTP-only matches the Finnhub / Yahoo wrappers
already in `backend/ingestion/`.

```python
# backend/alerts/channels/telegram.py
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential
from core.config import get_settings
from core.logging import get_logger
from alerts.channels.base import Channel, ChannelResult
from alerts.templates.telegram_render import render

log = get_logger(__name__)

class TelegramChannel:
    id = "telegram"

    def __init__(self, client: httpx.Client | None = None) -> None:
        s = get_settings()
        self._token = s.telegram_bot_token
        self._chat_id = s.telegram_chat_id
        self._parse_mode = s.telegram_parse_mode
        self._disable_preview = s.telegram_disable_preview
        self._client = client or httpx.Client(timeout=s.telegram_timeout_s)

    @property
    def configured(self) -> bool:
        return bool(self._token and self._chat_id)

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=1, max=10))
    def send(self, alert_type: str, payload: dict) -> ChannelResult:
        if not self.configured:
            log.warning("telegram.skip.unconfigured", alert_type=alert_type)
            return ChannelResult(False, None, "telegram_not_configured")
        text = render(alert_type, payload, parse_mode=self._parse_mode)
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        r = self._client.post(url, json={
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": self._parse_mode,
            "disable_web_page_preview": self._disable_preview,
        })
        # 429 → respect retry_after; tenacity backoff handles transient 5xx.
        if r.status_code == 429:
            retry_after = r.json().get("parameters", {}).get("retry_after", 1)
            log.warning("telegram.rate_limited", retry_after=retry_after)
            raise httpx.HTTPStatusError("429", request=r.request, response=r)
        r.raise_for_status()
        msg_id = str(r.json()["result"]["message_id"])
        log.info("telegram.sent", alert_type=alert_type, message_id=msg_id)
        return ChannelResult(True, msg_id, None)
```

Notes:
- Splits messages > 4096 chars by paragraph in `render()`; first message
  carries the headline, follow-ups are continuation chunks.
- MarkdownV2 requires escaping `_ * [ ] ( ) ~ \` > # + - = | { } . !`.
  The renderer must escape literal payload values (tickers, prices)
  before substitution; templates use a `{{ esc(value) }}` Jinja filter.

### Dispatcher wiring

`alerts/dispatcher.py` (to be created) loads channels by id from a
registry, looks up `alert_preferences.channels` for the alert type, and
fans out:

```python
CHANNELS: dict[str, Channel] = {"telegram": TelegramChannel()}

def dispatch(alert_type: str, payload: dict) -> None:
    pref = load_preference(alert_type)               # or default
    if not pref.enabled or in_quiet_hours(pref):
        return
    sent: list[str] = []
    for ch_id in pref.channels:
        ch = CHANNELS.get(ch_id)
        if ch is None:
            log.warning("dispatch.channel.unknown", channel=ch_id)
            continue
        result = ch.send(alert_type, payload)
        if result.delivered:
            sent.append(ch_id)
    persist_alert(alert_type, payload, sent)         # writes alerts row
```

Failure mode: if Telegram returns non-2xx after retries, log the error,
omit `"telegram"` from `channels_sent`, and continue with other channels
(per doc 03's "fall back to push" pattern, applied symmetrically).

### Schema impact

None. `alert_preferences.channels` is already a JSON array of strings;
`"telegram"` becomes a valid entry. `alerts.channels_sent` records it
verbatim. No migration needed.

### Test strategy

- `tests/test_telegram_channel.py` — `respx`-mocked `httpx.Client`,
  asserts the request body, parse mode, and that 429 triggers a retry.
  No real network.
- Snapshot test on the renderer output for a synthetic morning digest
  payload (syrupy), so template tweaks are reviewable as diffs.
- Integration smoke test gated behind `pytest -m live_telegram` that
  reads `.env` and posts "test" to the configured chat — opt-in only.

### Manual test endpoint

Add `POST /api/system/alerts/test?channel=telegram` to
`backend/api/main.py` (or a new `api/alerts.py`). It loads a canned
payload from `tests/fixtures/alerts/morning_digest.json` and dispatches
through the channel. Lets us verify the bot end-to-end before the
screener is producing real digests.

## Phase 2 — Digest rendering

Once `screener_results` is being written (doc 02) and the digest builder
in `alerts/triggers/digest.py` (doc 03) emits a structured payload
shaped like:

```json
{
  "as_of": "2026-05-02",
  "macro": { "vix": 14.2, "spy_above_200ema": true, "term": 0.92 },
  "screener_hits": [
    { "symbol": "AAPL", "config": "Conservative Wheel",
      "close": 172.40, "rsi": 32, "ivp": 67, "score": 0.81,
      "next_earnings_days": 38 }
  ],
  "earnings_today": [ { "symbol": "MSFT", "when": "AMC" } ],
  "positions_attention": []
}
```

…the Phase 1 renderer maps it to MarkdownV2 chunks. No code changes in
`TelegramChannel` itself — only new templates. Deep links use
`TIMEZONE`-localized URLs against the Tailscale hostname from
`docs/deploy.md`.

## Phase 3 — Inbound commands

Pull in `python-telegram-bot[ext]` and run a long-poll consumer as a
separate scheduler job (or its own process spawned from the FastAPI
lifespan when `TELEGRAM_BOT_TOKEN` is set). Allowed updates restricted
to `message` and `callback_query`; other update types ignored.

Initial commands, all read-only and reusing existing API handlers:

| Command | Backend call | Notes |
|---|---|---|
| `/status` | `GET /api/system/health` | "Last bar 2026-05-02, 47 symbols, evening job ok 6h ago." |
| `/macro` | `GET /api/macro/current` | One-line VIX / VIX9D / term / SPY regime. |
| `/earnings` | `GET /api/earnings/upcoming?days=7` | Watchlist earnings next 7 days. |
| `/snooze SYMBOL HOURS` | new `alerts/snooze.py` table | Suppresses alerts for symbol until expiry. |
| `/help` | static | Lists the above. |

Authorization: every update's `effective_chat.id` must equal
`TELEGRAM_CHAT_ID`. Anything else gets a single "unauthorized" reply and
is logged at WARNING. No multi-user support — explicit non-goal per doc
00.

## Phase 4 — Inline acks

Each setup / position alert appends an inline keyboard:

```
[ Ack ]   [ Snooze 1h ]   [ Open in UI ]
```

Callback data encodes `alert_id`. Handler updates
`alerts.user_acked = TRUE`, writes a snooze row, or deep-links — no
mutation beyond those two columns. Uses the same allow-list as Phase 3.

## Failure modes

- **Token revoked / wrong chat id** — first send returns 401/400; mark
  channel as failed in `channels_sent`, surface in `/api/system/health`
  as a warning ("telegram unconfigured or revoked").
- **Telegram outage** — tenacity retries 3× with exponential backoff;
  after that the alert is still persisted in `alerts` (so the in-app
  history shows it) with `telegram` absent from `channels_sent`.
- **Markdown escaping bug** — Telegram returns 400
  `Bad Request: can't parse entities`; the renderer wraps any failed
  parse in a fallback "send as plain text with `parse_mode=None`"
  retry, logged at ERROR so the template gets fixed.
- **Stale data guard** — same rule as doc 03: if last bar > 24h old
  during RTH, the dispatcher refuses to send intraday alerts. Digests
  still send but include a ⚠ "data stale" line.
- **Quiet hours** — applied at the dispatcher level (channel-agnostic),
  not inside `TelegramChannel`. Position-breach alerts can opt out per
  the existing override flag in doc 03.

## Out of scope (v1)

- Multi-chat / group broadcasting.
- Webhook mode (long-polling is simpler for a single VPS behind
  Tailscale and avoids exposing a public URL).
- Voice / photo messages.
- Bot inline mode (`@penny_pincher_bot AAPL` in any chat).
- Trade execution commands — alert-only product per doc 00.

## Build order checklist

Phase 1 only, in commit-sized chunks:

- [ ] `feat(core): add telegram_* settings + .env.example entries`
- [ ] `feat(alerts): channel base.py (Channel protocol + ChannelResult)`
- [ ] `feat(alerts): telegram channel adapter with httpx + tenacity`
- [ ] `feat(alerts): MarkdownV2 renderer + escape helper + first template`
- [ ] `feat(alerts): dispatcher with channel registry + preference loading`
- [ ] `feat(api): POST /api/system/alerts/test for manual verification`
- [ ] `test(alerts): respx-mocked telegram channel + renderer snapshots`

Phases 2–4 unblock once their named dependencies land.
