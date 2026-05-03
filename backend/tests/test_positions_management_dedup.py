"""Lifecycle-dedup coverage for ``positions.management.fire_triggers``.

Each scenario seeds an ``alerts`` table backed by a real (alembic-migrated)
SQLite DB and asserts that ``fire_triggers`` suppresses duplicates on a
re-run, but still fires for new ``(position_id, rule)`` pairs.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import select

from alembic import command
from alerts.triggers._dedup import already_dispatched_for_position_rule
from db import get_session
from db.models.alerts import Alert
from positions.management import ALERT_TYPE, Trigger, fire_triggers


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_path = tmp_path / "positions_dedup.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")

    from core.config import get_settings
    from db import session as db_session

    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()

    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    yield

    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()


def _trigger(rule: str, position_id: int = 1, symbol: str = "AAPL") -> Trigger:
    return Trigger(rule=rule, position_id=position_id, symbol=symbol, payload={"k": "v"})


def test_helper_returns_false_when_no_rows(db: None) -> None:
    with get_session() as session:
        assert already_dispatched_for_position_rule(session, position_id=1, rule="dte") is False


def test_helper_matches_position_id_and_rule(db: None) -> None:
    with get_session() as session:
        session.add(
            Alert(
                alert_type=ALERT_TYPE,
                symbol="AAPL",
                payload_json={"position_id": 1, "rule": "dte"},
            )
        )

    with get_session() as session:
        assert already_dispatched_for_position_rule(session, position_id=1, rule="dte")
        # Different rule → no match.
        assert not already_dispatched_for_position_rule(session, position_id=1, rule="delta_breach")
        # Different position → no match (lifecycle reset).
        assert not already_dispatched_for_position_rule(session, position_id=2, rule="dte")


def test_first_run_dispatches_all_and_persists(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    triggers = [_trigger("dte"), _trigger("delta_breach"), _trigger("near_strike")]

    captured: list[tuple[str, dict[str, object]]] = []

    def fake_dispatch(alert_type: str, payload: dict[str, object], **_: object) -> None:
        captured.append((alert_type, payload))
        symbol_val = payload.get("symbol")
        symbol = symbol_val if isinstance(symbol_val, str) else None
        # Mimic the real dispatcher: persist a row so future dedup sees it.
        with get_session() as session:
            session.add(
                Alert(
                    alert_type=alert_type,
                    symbol=symbol,
                    payload_json=dict(payload),
                )
            )

    monkeypatch.setattr("alerts.dispatcher.dispatch", fake_dispatch)

    with get_session() as session:
        result = fire_triggers(session, triggers)

    assert result.dispatched == 3
    assert result.suppressed == 0
    assert len(captured) == 3


def test_second_run_fully_suppresses_known_rules(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    # Pre-seed alerts as if a prior run already fired everything.
    with get_session() as session:
        for rule in ("dte", "delta_breach", "near_strike"):
            session.add(
                Alert(
                    alert_type=ALERT_TYPE,
                    symbol="AAPL",
                    payload_json={"position_id": 1, "rule": rule, "symbol": "AAPL"},
                )
            )

    triggers = [_trigger("dte"), _trigger("delta_breach"), _trigger("near_strike")]

    calls = 0

    def fake_dispatch(*_: object, **__: object) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr("alerts.dispatcher.dispatch", fake_dispatch)

    with get_session() as session:
        result = fire_triggers(session, triggers)

    assert result.dispatched == 0
    assert result.suppressed == 3
    assert calls == 0


def test_new_position_id_resets_lifecycle(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    # Position 1 already fired dte; position 2 is a fresh wheel cycle on the
    # same symbol — it should still get the alert.
    with get_session() as session:
        session.add(
            Alert(
                alert_type=ALERT_TYPE,
                symbol="AAPL",
                payload_json={"position_id": 1, "rule": "dte", "symbol": "AAPL"},
            )
        )

    triggers = [_trigger("dte", position_id=1), _trigger("dte", position_id=2)]

    dispatched_ids: list[int] = []

    def fake_dispatch(_: str, payload: dict[str, object], **__: object) -> None:
        position_id = payload["position_id"]
        assert isinstance(position_id, int)
        dispatched_ids.append(position_id)

    monkeypatch.setattr("alerts.dispatcher.dispatch", fake_dispatch)

    with get_session() as session:
        result = fire_triggers(session, triggers)

    assert result.dispatched == 1
    assert result.suppressed == 1
    assert dispatched_ids == [2]


def test_mixed_old_and_new_rules(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    # Position 1 has previously fired only `dte`. A new evaluation finds both
    # `dte` (suppress) and `delta_breach` (dispatch).
    with get_session() as session:
        session.add(
            Alert(
                alert_type=ALERT_TYPE,
                symbol="AAPL",
                payload_json={"position_id": 1, "rule": "dte", "symbol": "AAPL"},
            )
        )

    triggers = [_trigger("dte"), _trigger("delta_breach")]

    dispatched_rules: list[str] = []

    def fake_dispatch(_: str, payload: dict[str, object], **__: object) -> None:
        rule = payload["rule"]
        assert isinstance(rule, str)
        dispatched_rules.append(rule)

    monkeypatch.setattr("alerts.dispatcher.dispatch", fake_dispatch)

    with get_session() as session:
        result = fire_triggers(session, triggers)

    assert result.dispatched == 1
    assert result.suppressed == 1
    assert dispatched_rules == ["delta_breach"]


def test_empty_trigger_list_returns_zeros(db: None) -> None:
    with get_session() as session:
        result = fire_triggers(session, [])

    assert result.dispatched == 0
    assert result.suppressed == 0


def test_other_alert_types_do_not_count(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    # An unrelated alert type referencing the same position_id+rule must not
    # block a position_management dispatch.
    with get_session() as session:
        session.add(
            Alert(
                alert_type="some_other_family",
                symbol="AAPL",
                payload_json={"position_id": 1, "rule": "dte"},
            )
        )

    calls = 0

    def fake_dispatch(*_: object, **__: object) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr("alerts.dispatcher.dispatch", fake_dispatch)

    with get_session() as session:
        result = fire_triggers(session, [_trigger("dte")])

    assert result.dispatched == 1
    assert result.suppressed == 0
    assert calls == 1


def test_run_then_rerun_through_real_dispatcher_path(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: first run writes alerts via the real dispatcher; the
    second run must read those rows and fully suppress."""
    # Patch only the channel send so we don't try to hit Telegram, but let
    # the dispatcher persist rows normally.
    from alerts.channels.base import ChannelResult

    class FakeTelegram:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def send(self, alert_type: str, payload: dict[str, object]) -> ChannelResult:
            self.calls.append((alert_type, dict(payload)))
            return ChannelResult(True, "msg-1", None)

    fake = FakeTelegram()
    monkeypatch.setattr("alerts.dispatcher.CHANNELS", {"telegram": fake})

    triggers = [_trigger("dte"), _trigger("delta_breach")]

    with get_session() as session:
        first = fire_triggers(session, triggers)
    assert first.dispatched == 2
    assert first.suppressed == 0

    with get_session() as session:
        second = fire_triggers(session, triggers)
    assert second.dispatched == 0
    assert second.suppressed == 2

    with get_session() as session:
        rows = session.execute(select(Alert)).scalars().all()
    assert len(rows) == 2
    assert {row.payload_json["rule"] for row in rows} == {"dte", "delta_breach"}
