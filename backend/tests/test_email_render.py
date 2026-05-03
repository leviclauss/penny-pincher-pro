"""Snapshot + structural tests for the email (SMTP) template renderer.

Mirrors ``test_telegram_render.py`` — the snapshot guards against accidental
template drift; the structural tests pin down subject/body parsing and
deep-link behavior.
"""

from __future__ import annotations

import json
from pathlib import Path

from syrupy.assertion import SnapshotAssertion

from alerts.templates.email_render import render

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "alerts"


def _load(name: str) -> dict[str, object]:
    payload: dict[str, object] = json.loads((FIXTURES_DIR / name).read_text())
    return payload


def test_render_morning_digest_snapshot(snapshot: SnapshotAssertion) -> None:
    output = render("morning_digest", _load("morning_digest.json"))
    assert {"subject": output.subject, "body": output.body} == snapshot


def test_render_setup_triggered_snapshot(snapshot: SnapshotAssertion) -> None:
    output = render("setup_triggered", _load("setup_triggered.json"))
    assert {"subject": output.subject, "body": output.body} == snapshot


def test_render_iv_spike_snapshot(snapshot: SnapshotAssertion) -> None:
    output = render("iv_spike", _load("iv_spike.json"))
    assert {"subject": output.subject, "body": output.body} == snapshot


def test_render_position_management_snapshot(snapshot: SnapshotAssertion) -> None:
    output = render("position_management", _load("position_management.json"))
    assert {"subject": output.subject, "body": output.body} == snapshot


def test_render_no_escaping_for_special_chars() -> None:
    """Plain text — no MarkdownV2 escaping headaches."""
    output = render("setup_triggered", _load("setup_triggered.json"))
    # Date contains '-' which must remain literal in plain-text email.
    assert "2026-05-04" in output.subject or "2026-05-04" in output.body


def test_render_subject_is_first_line() -> None:
    output = render("morning_digest", _load("morning_digest.json"))
    assert output.subject.startswith("Wheel — Morning digest")
    # Body is everything after the blank-line separator.
    assert "Macro" in output.body
    assert "Morning digest" not in output.body


def test_render_includes_deep_link_when_web_base_url_set() -> None:
    output = render(
        "setup_triggered",
        _load("setup_triggered.json"),
        web_base_url="https://wheel.example.ts.net",
    )
    assert "https://wheel.example.ts.net/tickers/AAPL" in output.body


def test_render_omits_deep_link_when_web_base_url_blank() -> None:
    output = render("setup_triggered", _load("setup_triggered.json"))
    assert "http" not in output.body
