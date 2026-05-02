"""Unit tests for the Yahoo Finance v8 chart client."""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest
import respx

from ingestion.yahoo_client import IndexBarRecord, YahooClient, YahooError


def _payload(symbol: str, rows: list[tuple[date, float | None]]) -> dict[str, object]:
    timestamps = [int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp()) for d, _ in rows]
    closes = [c for _, c in rows]
    return {
        "chart": {
            "result": [
                {
                    "meta": {"symbol": symbol},
                    "timestamp": timestamps,
                    "indicators": {"quote": [{"close": closes}]},
                }
            ]
        }
    }


@respx.mock
def test_normalizes_index_history() -> None:
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX").mock(
        return_value=httpx.Response(
            200,
            json=_payload("^VIX", [(date(2026, 5, 1), 18.2), (date(2026, 5, 2), 17.9)]),
        )
    )
    out = YahooClient(base_url="https://query1.finance.yahoo.com").get_index_history(
        "^VIX", days_back=30
    )
    assert len(out) == 2
    assert isinstance(out[0], IndexBarRecord)
    assert out[0].symbol == "^VIX"
    assert out[0].date == date(2026, 5, 1)
    assert out[0].close == pytest.approx(18.2)


@respx.mock
def test_skips_null_closes() -> None:
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX").mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                "^VIX",
                [(date(2026, 5, 1), 18.2), (date(2026, 5, 2), None), (date(2026, 5, 3), 17.5)],
            ),
        )
    )
    out = YahooClient(base_url="https://query1.finance.yahoo.com").get_index_history(
        "^VIX", days_back=30
    )
    dates = [r.date for r in out]
    assert dates == [date(2026, 5, 1), date(2026, 5, 3)]


@respx.mock
def test_handles_empty_result() -> None:
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX").mock(
        return_value=httpx.Response(200, json={"chart": {"result": []}})
    )
    out = YahooClient(base_url="https://query1.finance.yahoo.com").get_index_history(
        "^VIX", days_back=30
    )
    assert out == []


@respx.mock
def test_retries_on_5xx() -> None:
    route = respx.get("https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(200, json=_payload("^VIX", [(date(2026, 5, 1), 18.0)])),
        ]
    )
    out = YahooClient(base_url="https://query1.finance.yahoo.com").get_index_history(
        "^VIX", days_back=30
    )
    assert route.call_count == 2
    assert len(out) == 1


@respx.mock
def test_unexpected_payload_raises() -> None:
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX").mock(
        return_value=httpx.Response(200, json="not-a-dict")
    )
    with pytest.raises(YahooError):
        YahooClient(base_url="https://query1.finance.yahoo.com").get_index_history(
            "^VIX", days_back=30
        )
