"""Snapshot + structural tests for the ntfy template renderer.

Mirrors ``test_telegram_render.py`` — the snapshot guards against accidental
template drift; the structural tests pin down title/body parsing.
"""

from __future__ import annotations

import json
from pathlib import Path

from syrupy.assertion import SnapshotAssertion

from alerts.templates.ntfy_render import render

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "alerts"


def _load(name: str) -> dict[str, object]:
    payload: dict[str, object] = json.loads((FIXTURES_DIR / name).read_text())
    return payload


def test_render_morning_digest_snapshot(snapshot: SnapshotAssertion) -> None:
    output = render("morning_digest", _load("morning_digest.json"))
    assert {"title": output.title, "body": output.body} == snapshot


def test_render_setup_triggered_snapshot(snapshot: SnapshotAssertion) -> None:
    output = render("setup_triggered", _load("setup_triggered.json"))
    assert {"title": output.title, "body": output.body} == snapshot


def test_render_iv_spike_snapshot(snapshot: SnapshotAssertion) -> None:
    output = render("iv_spike", _load("iv_spike.json"))
    assert {"title": output.title, "body": output.body} == snapshot


def test_render_position_management_snapshot(snapshot: SnapshotAssertion) -> None:
    output = render("position_management", _load("position_management.json"))
    assert {"title": output.title, "body": output.body} == snapshot


def test_title_is_short() -> None:
    """Phone notifications truncate after ~80 chars; keep titles tight."""
    for name in (
        "morning_digest.json",
        "setup_triggered.json",
        "iv_spike.json",
        "position_management.json",
    ):
        alert_type = name.removesuffix(".json")
        output = render(alert_type, _load(name))
        assert len(output.title) <= 80, f"{alert_type}: {output.title!r}"
        assert "Title:" not in output.title
