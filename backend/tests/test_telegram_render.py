"""Tests for the Telegram template renderer + MarkdownV2 escape helper.

Snapshot test on the morning_digest template guards against accidental
template drift; the escape unit tests pin down the per-character behavior
that Telegram's parser cares about.
"""

from __future__ import annotations

import json
from pathlib import Path

from syrupy.assertion import SnapshotAssertion

from alerts.templates.telegram_render import (
    escape_html,
    escape_markdown_v2,
    render,
    split_for_telegram,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "alerts"
FIXTURE = FIXTURES_DIR / "morning_digest.json"
SETUP_FIXTURE = FIXTURES_DIR / "setup_triggered.json"
IV_SPIKE_FIXTURE = FIXTURES_DIR / "iv_spike.json"


def test_escape_markdown_v2_handles_all_specials() -> None:
    raw = "_*[]()~`>#+-=|{}.!\\"
    out = escape_markdown_v2(raw)
    for char in raw:
        assert f"\\{char}" in out
    # Plain text untouched.
    assert escape_markdown_v2("AAPL 2026") == "AAPL 2026"


def test_escape_markdown_v2_coerces_non_strings() -> None:
    assert escape_markdown_v2(172.4) == "172\\.4"
    assert escape_markdown_v2(7) == "7"


def test_escape_html_translates_three_chars() -> None:
    assert escape_html("<a & b>") == "&lt;a &amp; b&gt;"


def test_render_morning_digest_snapshot(snapshot: SnapshotAssertion) -> None:
    payload = json.loads(FIXTURE.read_text())
    output = render("morning_digest", payload, parse_mode="MarkdownV2")
    assert output == snapshot


def test_render_escapes_payload_values() -> None:
    payload = {
        "as_of": "2026-05-02",
        "macro": {"vix": 14.2, "spy_above_200ema": True, "term": 0.92},
        "screener_hits": [],
        "earnings_today": [],
        "positions_attention": [],
    }
    output = render("morning_digest", payload, parse_mode="MarkdownV2")
    # Date contains '-' which must be escaped.
    assert "2026\\-05\\-02" in output
    # VIX rendered as "14.1" (rounded) → escaped period.
    assert "14\\.2" in output


def test_split_returns_single_chunk_when_short() -> None:
    assert split_for_telegram("hello") == ["hello"]


def test_split_breaks_on_paragraphs() -> None:
    text = "para one\n\n" + ("x" * 100) + "\n\n" + ("y" * 100)
    chunks = split_for_telegram(text, limit=120)
    assert len(chunks) >= 2
    assert all(len(c) <= 120 for c in chunks)


def test_split_hard_splits_oversized_paragraph() -> None:
    text = "z" * 250
    chunks = split_for_telegram(text, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks) == text


def test_render_setup_triggered_snapshot(snapshot: SnapshotAssertion) -> None:
    payload = json.loads(SETUP_FIXTURE.read_text())
    output = render("setup_triggered", payload, parse_mode="MarkdownV2")
    assert output == snapshot


def test_render_iv_spike_snapshot(snapshot: SnapshotAssertion) -> None:
    payload = json.loads(IV_SPIKE_FIXTURE.read_text())
    output = render("iv_spike", payload, parse_mode="MarkdownV2")
    assert output == snapshot
