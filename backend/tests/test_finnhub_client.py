"""Unit tests for the Finnhub HTTP client.

Uses respx to mock httpx. Verifies normalization, retry on 5xx, missing-key
guard, and tolerance for malformed entries (skip-not-crash).
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from ingestion.finnhub_client import (
    CompanyProfile,
    EarningsRecord,
    FinnhubClient,
    FinnhubError,
)


def _payload() -> dict[str, object]:
    return {
        "earningsCalendar": [
            {"symbol": "AAPL", "date": "2026-05-02", "hour": "amc"},
            {"symbol": "MSFT", "date": "2026-05-03", "hour": "bmo"},
            {"symbol": "AMZN", "date": "2026-05-04", "hour": ""},
            {"symbol": "TSLA", "date": "2026-05-05", "hour": "weird"},
        ]
    }


@respx.mock
def test_get_calendar_normalizes_and_filters_bad_rows() -> None:
    route = respx.get("https://finnhub.io/api/v1/calendar/earnings").mock(
        return_value=httpx.Response(200, json=_payload())
    )
    client = FinnhubClient(api_key="k", base_url="https://finnhub.io/api/v1")
    out = client.get_earnings_calendar(from_date=date(2026, 5, 1), to_date=date(2026, 6, 1))

    assert route.called
    assert len(out) == 4
    by_symbol = {r.symbol: r for r in out}
    assert isinstance(by_symbol["AAPL"], EarningsRecord)
    assert by_symbol["AAPL"].time_of_day == "AMC"
    assert by_symbol["MSFT"].time_of_day == "BMO"
    assert by_symbol["AMZN"].time_of_day is None
    assert by_symbol["TSLA"].time_of_day == "unknown"


@respx.mock
def test_passes_query_params() -> None:
    route = respx.get("https://finnhub.io/api/v1/calendar/earnings").mock(
        return_value=httpx.Response(200, json={"earningsCalendar": []})
    )
    FinnhubClient(api_key="secret", base_url="https://finnhub.io/api/v1").get_earnings_calendar(
        from_date=date(2026, 5, 1), to_date=date(2026, 6, 1), symbol="AAPL"
    )
    request = route.calls.last.request
    assert request.url.params["from"] == "2026-05-01"
    assert request.url.params["to"] == "2026-06-01"
    assert request.url.params["symbol"] == "AAPL"
    assert request.url.params["token"] == "secret"


@respx.mock
def test_retries_on_500_then_succeeds() -> None:
    route = respx.get("https://finnhub.io/api/v1/calendar/earnings").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(500),
            httpx.Response(200, json={"earningsCalendar": []}),
        ]
    )
    out = FinnhubClient(api_key="k", base_url="https://finnhub.io/api/v1").get_earnings_calendar(
        from_date=date(2026, 5, 1), to_date=date(2026, 6, 1)
    )
    assert route.call_count == 3
    assert out == []


@respx.mock
def test_skips_entries_with_bad_dates() -> None:
    payload = {
        "earningsCalendar": [
            {"symbol": "AAPL", "date": "not-a-date", "hour": "amc"},
            {"symbol": "MSFT", "date": "2026-05-03", "hour": "bmo"},
        ]
    }
    respx.get("https://finnhub.io/api/v1/calendar/earnings").mock(
        return_value=httpx.Response(200, json=payload)
    )
    out = FinnhubClient(api_key="k", base_url="https://finnhub.io/api/v1").get_earnings_calendar(
        from_date=date(2026, 5, 1), to_date=date(2026, 6, 1)
    )
    assert [r.symbol for r in out] == ["MSFT"]


@respx.mock
def test_handles_missing_calendar_key() -> None:
    respx.get("https://finnhub.io/api/v1/calendar/earnings").mock(
        return_value=httpx.Response(200, json={})
    )
    out = FinnhubClient(api_key="k", base_url="https://finnhub.io/api/v1").get_earnings_calendar(
        from_date=date(2026, 5, 1), to_date=date(2026, 6, 1)
    )
    assert out == []


def test_missing_api_key_raises() -> None:
    with pytest.raises(FinnhubError):
        FinnhubClient(api_key="")


@respx.mock
def test_get_company_profile_normalizes() -> None:
    payload = {
        "name": "Apple Inc",
        "ticker": "AAPL",
        "finnhubIndustry": "Technology",
        "marketCapitalization": 3_500_000.0,  # millions
        "country": "US",
    }
    respx.get("https://finnhub.io/api/v1/stock/profile2").mock(
        return_value=httpx.Response(200, json=payload)
    )
    out = FinnhubClient(api_key="k", base_url="https://finnhub.io/api/v1").get_company_profile(
        "AAPL"
    )
    assert isinstance(out, CompanyProfile)
    assert out.symbol == "AAPL"
    assert out.name == "Apple Inc"
    assert out.sector == "Technology"
    assert out.market_cap == 3_500_000.0 * 1_000_000


@respx.mock
def test_get_company_profile_returns_none_for_empty_payload() -> None:
    respx.get("https://finnhub.io/api/v1/stock/profile2").mock(
        return_value=httpx.Response(200, json={})
    )
    out = FinnhubClient(api_key="k", base_url="https://finnhub.io/api/v1").get_company_profile(
        "QQQ"
    )
    assert out is None


@respx.mock
def test_get_company_profile_partial_fields() -> None:
    payload = {"name": "Foo", "finnhubIndustry": "", "marketCapitalization": 0}
    respx.get("https://finnhub.io/api/v1/stock/profile2").mock(
        return_value=httpx.Response(200, json=payload)
    )
    out = FinnhubClient(api_key="k", base_url="https://finnhub.io/api/v1").get_company_profile(
        "FOO"
    )
    assert out is not None
    assert out.name == "Foo"
    assert out.sector is None
    assert out.market_cap is None
