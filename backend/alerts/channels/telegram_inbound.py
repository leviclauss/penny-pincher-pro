"""Inbound Telegram bot: long-polling consumer for ``/status``, ``/snooze``,
``/help``, plus the inline-ack ``callback_query`` handler.

Phases 3-4 of ``docs/planning/09-telegram-integration.md``. Long-poll mode
keeps the deployment story simple — no public webhook URL to manage. A
single thread (spawned from FastAPI's lifespan when ``TELEGRAM_INBOUND_ENABLED``
is true) drives the loop; updates land via ``getUpdates`` and are dispatched
to small synchronous handlers that reuse the existing API/db layer.

Every update is filtered through the allow-listed ``TELEGRAM_CHAT_ID``;
anything else gets a single "unauthorized" log + drop. The next-update
offset is persisted in ``bot_state`` so a process restart resumes cleanly
without replaying old commands or losing acks.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select

from alerts.channels.telegram import API_BASE
from alerts.dispatcher import GLOBAL_SNOOZE_KEY
from alerts.templates.telegram_render import escape_markdown_v2
from core.config import get_settings
from core.logging import get_logger
from core.time import utcnow
from db import get_session
from db.models.alerts import Alert, AlertPreference
from db.models.market import BarDaily
from db.models.system import BotState, JobRun

log = get_logger(__name__)

OFFSET_KEY = "telegram_update_offset"

ALLOWED_UPDATE_TYPES = ("message", "callback_query")

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([mhd])\s*$", re.IGNORECASE)
_DURATION_UNITS = {
    "m": "minutes",
    "h": "hours",
    "d": "days",
}


@dataclass(frozen=True, slots=True)
class HandledUpdate:
    """Outcome of handling one update — used by tests and heartbeat logging."""

    kind: str  # "command", "callback", "ignored", "unauthorized"
    detail: str | None = None


def parse_snooze_duration(arg: str) -> timedelta | None:
    """Parse ``30m`` / ``2h`` / ``1d`` into a ``timedelta``. Returns None on bad input."""
    match = _DURATION_RE.match(arg)
    if not match:
        return None
    amount, unit = match.groups()
    n = int(amount)
    if n <= 0:
        return None
    kw = {_DURATION_UNITS[unit.lower()]: n}
    return timedelta(**kw)


class TelegramInboundBot:
    """Long-polling inbound consumer.

    Constructed once per process; ``run_forever`` blocks on the calling
    thread. ``poll_once`` returns the count of updates handled in a single
    cycle and is the entry point exercised by tests.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        allowed_chat_id: str | None = None,
        long_poll_s: int | None = None,
        idle_sleep_s: float | None = None,
        max_failures: int | None = None,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        settings = get_settings()
        self._token = token if token is not None else settings.telegram_bot_token
        self._allowed_chat_id = (
            allowed_chat_id if allowed_chat_id is not None else settings.telegram_chat_id
        )
        self._long_poll_s = (
            long_poll_s if long_poll_s is not None else settings.telegram_inbound_long_poll_s
        )
        self._idle_sleep_s = (
            idle_sleep_s if idle_sleep_s is not None else settings.telegram_inbound_idle_sleep_s
        )
        self._max_failures = (
            max_failures if max_failures is not None else settings.telegram_inbound_max_failures
        )
        # HTTP read timeout slightly longer than the long-poll value so the
        # request can return cleanly when no updates are pending.
        timeout = float(self._long_poll_s) + 5.0
        self._client = client or httpx.Client(timeout=timeout)
        self._clock = clock or utcnow
        self._stop = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self._token and self._allowed_chat_id)

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        if not self.configured:
            log.warning("telegram_inbound.skip.unconfigured")
            return
        log.info(
            "telegram_inbound.start",
            chat_id=self._allowed_chat_id,
            long_poll_s=self._long_poll_s,
        )
        consecutive_failures = 0
        while not self._stop.is_set():
            try:
                self.poll_once()
                consecutive_failures = 0
            except Exception as exc:
                consecutive_failures += 1
                log.error(
                    "telegram_inbound.poll_failed",
                    error=str(exc),
                    consecutive_failures=consecutive_failures,
                )
                if consecutive_failures >= self._max_failures:
                    log.error(
                        "telegram_inbound.aborting",
                        consecutive_failures=consecutive_failures,
                    )
                    break
                # Back off briefly on transient errors so we don't hot-loop.
                self._stop.wait(timeout=min(2**consecutive_failures, 30))
        log.info("telegram_inbound.stopped")

    # ------------------------------------------------------------------
    # Polling cycle
    # ------------------------------------------------------------------
    def poll_once(self) -> int:
        """One ``getUpdates`` round + dispatch. Returns the # of updates handled."""
        offset = _load_offset()
        params: dict[str, Any] = {
            "timeout": self._long_poll_s,
            "allowed_updates": list(ALLOWED_UPDATE_TYPES),
        }
        if offset is not None:
            params["offset"] = offset

        url = f"{API_BASE}/bot{self._token}/getUpdates"
        response = self._client.get(url, params=params)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not body.get("ok"):
            raise RuntimeError(f"telegram getUpdates failed: {body!r}")
        updates = body.get("result")
        if not isinstance(updates, list):
            return 0

        handled = 0
        max_update_id = -1
        for update in updates:
            if not isinstance(update, dict):
                continue
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                max_update_id = max(max_update_id, update_id)
            try:
                self.handle_update(update)
            except Exception as exc:
                log.error(
                    "telegram_inbound.handler_error",
                    update_id=update_id,
                    error=str(exc),
                )
            handled += 1

        if max_update_id >= 0:
            _save_offset(max_update_id + 1)
        return handled

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def handle_update(self, update: dict[str, Any]) -> HandledUpdate:
        if "callback_query" in update:
            return self._handle_callback(update["callback_query"])
        if "message" in update:
            return self._handle_message(update["message"])
        return HandledUpdate(kind="ignored", detail="no_handler")

    def _handle_message(self, message: dict[str, Any]) -> HandledUpdate:
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not self._is_authorized(chat_id):
            log.warning("telegram_inbound.unauthorized.message", chat_id=chat_id)
            self._reply(chat_id, "unauthorized", parse=False)
            return HandledUpdate(kind="unauthorized", detail=str(chat_id))

        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            return HandledUpdate(kind="ignored", detail="non_command")

        cmd, _, arg = text.partition(" ")
        # Strip optional ``@botname`` suffix (e.g. ``/status@MyBot``).
        cmd = cmd.split("@", 1)[0].lower()
        arg = arg.strip()

        if cmd == "/status":
            self._reply(chat_id, _build_status_reply())
            return HandledUpdate(kind="command", detail="status")
        if cmd == "/snooze":
            reply = _apply_snooze(arg, now=self._clock())
            self._reply(chat_id, reply)
            return HandledUpdate(kind="command", detail=f"snooze:{arg}")
        if cmd == "/help":
            self._reply(chat_id, _help_text())
            return HandledUpdate(kind="command", detail="help")

        self._reply(chat_id, _help_text())
        return HandledUpdate(kind="ignored", detail=f"unknown_command:{cmd}")

    def _handle_callback(self, query: dict[str, Any]) -> HandledUpdate:
        message = query.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        callback_id = query.get("id")
        data = query.get("data") or ""

        if not self._is_authorized(chat_id):
            log.warning("telegram_inbound.unauthorized.callback", chat_id=chat_id)
            self._answer_callback(callback_id, text="unauthorized")
            return HandledUpdate(kind="unauthorized", detail=str(chat_id))

        if not data.startswith("ack:"):
            self._answer_callback(callback_id, text="unsupported")
            return HandledUpdate(kind="ignored", detail=f"unknown_callback:{data}")

        try:
            alert_id = int(data.split(":", 1)[1])
        except (ValueError, IndexError):
            self._answer_callback(callback_id, text="bad alert id")
            return HandledUpdate(kind="ignored", detail=f"bad_alert_id:{data}")

        acked = _ack_alert(alert_id)
        if not acked:
            self._answer_callback(callback_id, text="alert not found")
            return HandledUpdate(kind="callback", detail=f"missing:{alert_id}")

        self._answer_callback(callback_id, text="Acked")
        message_id = message.get("message_id")
        if isinstance(message_id, int):
            self._edit_keyboard_acked(chat_id, message_id)
        log.info("telegram_inbound.ack", alert_id=alert_id)
        return HandledUpdate(kind="callback", detail=f"ack:{alert_id}")

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------
    def _is_authorized(self, chat_id: Any) -> bool:
        if chat_id is None:
            return False
        return str(chat_id) == str(self._allowed_chat_id)

    # ------------------------------------------------------------------
    # Outbound replies (small subset of the Bot API)
    # ------------------------------------------------------------------
    def _reply(self, chat_id: Any, text: str, *, parse: bool = True) -> None:
        if chat_id is None:
            return
        url = f"{API_BASE}/bot{self._token}/sendMessage"
        body: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse:
            body["parse_mode"] = "MarkdownV2"
        try:
            response = self._client.post(url, json=body)
            response.raise_for_status()
        except Exception as exc:
            log.error("telegram_inbound.reply_failed", error=str(exc))

    def _answer_callback(self, callback_id: Any, *, text: str) -> None:
        if callback_id is None:
            return
        url = f"{API_BASE}/bot{self._token}/answerCallbackQuery"
        try:
            response = self._client.post(
                url,
                json={"callback_query_id": callback_id, "text": text},
            )
            response.raise_for_status()
        except Exception as exc:
            log.error("telegram_inbound.answer_callback_failed", error=str(exc))

    def _edit_keyboard_acked(self, chat_id: Any, message_id: int) -> None:
        url = f"{API_BASE}/bot{self._token}/editMessageReplyMarkup"
        try:
            response = self._client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reply_markup": {
                        "inline_keyboard": [[{"text": "✓ Acked", "callback_data": "noop"}]]
                    },
                },
            )
            response.raise_for_status()
        except Exception as exc:
            log.warning("telegram_inbound.edit_keyboard_failed", error=str(exc))


# ----------------------------------------------------------------------
# Persistence helpers — kept as module-level so handlers stay testable.
# ----------------------------------------------------------------------
def _load_offset() -> int | None:
    with get_session() as session:
        row = session.execute(
            select(BotState).where(BotState.key == OFFSET_KEY)
        ).scalar_one_or_none()
        if row is None:
            return None
        try:
            return int(row.value)
        except ValueError:
            return None


def _save_offset(offset: int) -> None:
    with get_session() as session:
        row = session.execute(
            select(BotState).where(BotState.key == OFFSET_KEY)
        ).scalar_one_or_none()
        now = utcnow()
        if row is None:
            session.add(BotState(key=OFFSET_KEY, value=str(offset), updated_at=now))
        else:
            row.value = str(offset)
            row.updated_at = now


def _ack_alert(alert_id: int) -> bool:
    with get_session() as session:
        row = session.get(Alert, alert_id)
        if row is None:
            return False
        row.user_acked = True
    return True


# ----------------------------------------------------------------------
# Command outputs
# ----------------------------------------------------------------------
def _help_text() -> str:
    """Return the static MarkdownV2-escaped help blurb."""
    body = (
        "Penny Pincher commands:\n"
        "/status — last bar, recent jobs, unacked alerts\n"
        "/snooze 30m|2h|1d — silence all alerts for the duration\n"
        "/snooze off — clear an active snooze\n"
        "/help — this message"
    )
    return escape_markdown_v2(body)


def _build_status_reply() -> str:
    """Build the ``/status`` reply: latest bar, recent jobs, unacked count."""
    with get_session() as session:
        last_bar = session.execute(select(func.max(BarDaily.date))).scalar_one_or_none()

        # Latest run per job_name.
        latest_ids_subq = (
            select(func.max(JobRun.id).label("id")).group_by(JobRun.job_name).subquery()
        )
        rows = (
            session.execute(
                select(JobRun)
                .join(latest_ids_subq, JobRun.id == latest_ids_subq.c.id)
                .order_by(JobRun.job_name)
            )
            .scalars()
            .all()
        )
        last_runs: list[tuple[str, str, datetime | None]] = [
            (r.job_name, r.status, r.ended_at or r.started_at) for r in rows
        ]

        cutoff = utcnow() - timedelta(hours=24)
        unacked = session.execute(
            select(func.count())
            .select_from(Alert)
            .where(Alert.triggered_at >= cutoff)
            .where(Alert.user_acked.is_(False))
        ).scalar_one()

        global_pref = session.execute(
            select(AlertPreference).where(AlertPreference.alert_type == GLOBAL_SNOOZE_KEY)
        ).scalar_one_or_none()
        snooze_until = global_pref.snooze_until if global_pref is not None else None

    lines: list[str] = ["*Status*"]
    bar_line = f"Last bar: {last_bar.isoformat()}" if last_bar is not None else "Last bar: none"
    lines.append(escape_markdown_v2(bar_line))

    if last_runs:
        lines.append(escape_markdown_v2("Jobs:"))
        for name, status, when in last_runs:
            when_str = _humanize_age(when) if when is not None else "never"
            lines.append(escape_markdown_v2(f"- {name}: {status} ({when_str})"))
    else:
        lines.append(escape_markdown_v2("Jobs: no runs yet"))

    lines.append(escape_markdown_v2(f"Unacked alerts (24h): {int(unacked)}"))

    if snooze_until is not None:
        if snooze_until.tzinfo is None:
            snooze_until = snooze_until.replace(tzinfo=UTC)
        if utcnow() < snooze_until:
            lines.append(
                escape_markdown_v2(f"Snoozed until: {snooze_until.astimezone(UTC).isoformat()}")
            )

    return "\n".join(lines)


def _humanize_age(when: datetime) -> str:
    """Coarse "Nh Mm ago" string. Anchored to ``utcnow`` so tests stay stable."""
    # SQLite drops tz info on read — coerce naive timestamps back to UTC so
    # arithmetic stays consistent across SQLite and Postgres deployments.
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    delta = utcnow() - when
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m ago"
    days = seconds // 86400
    return f"{days}d ago"


def _apply_snooze(arg: str, *, now: datetime) -> str:
    """Persist the global snooze-until timestamp (or clear it).

    Returns a plain-language reply suitable for sending back to the user
    (already MarkdownV2-escaped).
    """
    arg_lower = arg.lower().strip()
    if arg_lower == "" or arg_lower in {"off", "clear", "stop"}:
        _set_global_snooze(None)
        if arg_lower in {"off", "clear", "stop"}:
            return escape_markdown_v2("Snooze cleared.")
        return escape_markdown_v2("Usage: /snooze 30m | 2h | 1d | off")

    duration = parse_snooze_duration(arg_lower)
    if duration is None:
        return escape_markdown_v2(
            "Couldn't parse duration. Try /snooze 30m, /snooze 2h, /snooze 1d, or /snooze off."
        )
    cutoff = now + duration
    _set_global_snooze(cutoff)
    body = f"Snoozed alerts for {arg_lower} (until {cutoff.astimezone(UTC).isoformat()})."
    return escape_markdown_v2(body)


def _set_global_snooze(snooze_until: datetime | None) -> None:
    """Upsert the ``__global__`` AlertPreference row with the new cutoff."""
    with get_session() as session:
        row = session.execute(
            select(AlertPreference).where(AlertPreference.alert_type == GLOBAL_SNOOZE_KEY)
        ).scalar_one_or_none()
        if row is None:
            session.add(
                AlertPreference(
                    alert_type=GLOBAL_SNOOZE_KEY,
                    channels=[],
                    enabled=True,
                    snooze_until=snooze_until,
                )
            )
        else:
            row.snooze_until = snooze_until
