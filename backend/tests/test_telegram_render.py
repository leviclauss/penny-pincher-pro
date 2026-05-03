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
POSITION_MANAGEMENT_FIXTURE = FIXTURES_DIR / "position_management.json"


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


def test_render_position_management_snapshot(snapshot: SnapshotAssertion) -> None:
    payload = json.loads(POSITION_MANAGEMENT_FIXTURE.read_text())
    output = render("position_management", payload, parse_mode="MarkdownV2")
    assert output == snapshot


_POSITION_RULE_PAYLOADS: dict[str, dict[str, float | int]] = {
    "pct_max_profit": {"pct_max_profit": 0.62, "threshold": 0.50},
    "dte": {"dte": 14, "threshold": 21},
    "delta_breach": {"delta": -0.48, "threshold": 0.45},
    "near_strike": {
        "underlying": 169.50,
        "strike": 170.00,
        "diff_pct": 0.0029,
        "threshold": 0.02,
    },
    "cc_itm_short_dte": {"dte": 5, "underlying": 178.20, "strike": 175.00},
    "stale_position": {"days_open": 73, "threshold": 60},
}


def test_render_position_management_all_rules() -> None:
    """Every supported rule renders without StrictUndefined errors."""
    for rule, extras in _POSITION_RULE_PAYLOADS.items():
        payload: dict[str, object] = {
            "rule": rule,
            "position_id": 7,
            "symbol": "AAPL",
            **extras,
        }
        output = render("position_management", payload, parse_mode="MarkdownV2")
        assert output.startswith("*Position Alert*"), f"rule={rule}: {output!r}"
        # No literal Jinja markup leaks through.
        assert "{%" not in output
        assert "{{" not in output


def test_render_morning_digest_with_web_base_url() -> None:
    """Deep links appear when web_base_url is set; the URL is left raw."""
    payload = json.loads(FIXTURE.read_text())
    output = render(
        "morning_digest",
        payload,
        parse_mode="MarkdownV2",
        web_base_url="https://wheel.example.ts.net",
    )
    assert "[open](https://wheel.example.ts.net/tickers/AAPL)" in output
    assert "[open](https://wheel.example.ts.net/tickers/MSFT)" in output


def test_render_position_management_with_web_base_url() -> None:
    payload = json.loads(POSITION_MANAGEMENT_FIXTURE.read_text())
    output = render(
        "position_management",
        payload,
        parse_mode="MarkdownV2",
        web_base_url="https://wheel.example.ts.net/",  # trailing slash gets normalized
    )
    assert "[open](https://wheel.example.ts.net/positions/42)" in output


def test_render_skips_links_when_web_base_url_blank() -> None:
    payload = json.loads(FIXTURE.read_text())
    output = render("morning_digest", payload, parse_mode="MarkdownV2")
    assert "[open]" not in output
