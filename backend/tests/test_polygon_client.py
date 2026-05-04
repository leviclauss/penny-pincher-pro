"""Unit tests for the Polygon options snapshot client.

Uses respx to mock httpx. Verifies normalization (including OI + volume that
Alpaca's free feed leaves NULL), pagination follow-through via ``next_url``,
auth header, server-side filter params, retry on 5xx, malformed-row tolerance,
and the missing-key guard.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from ingestion.options_client import OptionSnapshotRecord
from ingestion.polygon_client import PolygonError, PolygonOptionsClient


def _entry(
    *,
    ticker: str,
    contract_type: str,
    strike: float,
    expiration: str,
    bid: float | None = 1.20,
    ask: float | None = 1.25,
    last: float | None = 1.22,
    volume: int | None = 350,
    open_interest: int | None = 4200,
    iv: float | None = 0.27,
    delta: float | None = 0.42,
) -> dict[str, object]:
    return {
        "details": {
            "ticker": ticker,
            "contract_type": contract_type,
            "strike_price": strike,
            "expiration_date": expiration,
        },
        "last_quote": {"bid": bid, "ask": ask},
        "last_trade": {"price": last},
        "day": {"volume": volume},
        "greeks": {"delta": delta, "gamma": 0.04, "theta": -0.05, "vega": 0.12},
        "implied_volatility": iv,
        "open_interest": open_interest,
    }


def _payload(*entries: dict[str, object], next_url: str | None = None) -> dict[str, object]:
    out: dict[str, object] = {"results": list(entries), "status": "OK"}
    if next_url is not None:
        out["next_url"] = next_url
    return out


@respx.mock
def test_get_chain_normalizes_with_oi_and_volume() -> None:
    payload = _payload(
        _entry(
            ticker="O:AAPL240517C00170000",
            contract_type="call",
            strike=170.0,
            expiration="2024-05-17",
            volume=512,
            open_interest=8123,
        ),
        _entry(
            ticker="O:AAPL240517P00170000",
            contract_type="put",
            strike=170.0,
            expiration="2024-05-17",
            bid=1.95,
            ask=2.00,
            last=1.97,
            volume=300,
            open_interest=5400,
            delta=-0.40,
        ),
    )
    respx.get("https://api.polygon.io/v3/snapshot/options/AAPL").mock(
        return_value=httpx.Response(200, json=payload)
    )

    client = PolygonOptionsClient(api_key="k", base_url="https://api.polygon.io")
    out = client.get_chain("AAPL")

    assert len(out) == 2
    assert all(isinstance(r, OptionSnapshotRecord) for r in out)
    by_type = {r.option_type: r for r in out}
    call = by_type["call"]
    assert call.symbol == "AAPL"
    assert call.strike == 170.0
    assert call.expiration == date(2024, 5, 17)
    assert call.bid == 1.20
    assert call.ask == 1.25
    assert call.last == 1.22
    assert call.volume == 512
    assert call.open_interest == 8123
    assert call.iv == 0.27
    assert call.delta == 0.42

    put = by_type["put"]
    assert put.delta == -0.40
    assert put.volume == 300
    assert put.open_interest == 5400


@respx.mock
def test_uses_bearer_auth_header() -> None:
    route = respx.get("https://api.polygon.io/v3/snapshot/options/AAPL").mock(
        return_value=httpx.Response(200, json=_payload())
    )
    PolygonOptionsClient(api_key="secret", base_url="https://api.polygon.io").get_chain("AAPL")
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer secret"


@respx.mock
def test_passes_server_side_filters() -> None:
    route = respx.get("https://api.polygon.io/v3/snapshot/options/AAPL").mock(
        return_value=httpx.Response(200, json=_payload())
    )
    PolygonOptionsClient(api_key="k", base_url="https://api.polygon.io").get_chain(
        "AAPL",
        expiration_gte=date(2024, 5, 1),
        expiration_lte=date(2024, 7, 1),
        strike_gte=150.0,
        strike_lte=190.0,
    )
    params = route.calls.last.request.url.params
    assert params["expiration_date.gte"] == "2024-05-01"
    assert params["expiration_date.lte"] == "2024-07-01"
    assert params["strike_price.gte"] == "150.0000"
    assert params["strike_price.lte"] == "190.0000"
    assert params["limit"] == "250"


@respx.mock
def test_follows_next_url_pagination() -> None:
    page1 = _payload(
        _entry(
            ticker="O:AAPL240517C00170000",
            contract_type="call",
            strike=170.0,
            expiration="2024-05-17",
        ),
        next_url="https://api.polygon.io/v3/snapshot/options/AAPL?cursor=abc",
    )
    page2 = _payload(
        _entry(
            ticker="O:AAPL240517P00170000",
            contract_type="put",
            strike=170.0,
            expiration="2024-05-17",
        )
    )
    respx.get("https://api.polygon.io/v3/snapshot/options/AAPL").mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
        ]
    )

    out = PolygonOptionsClient(api_key="k", base_url="https://api.polygon.io").get_chain("AAPL")
    assert len(out) == 2
    assert {r.option_type for r in out} == {"call", "put"}


@respx.mock
def test_retries_on_500_then_succeeds() -> None:
    route = respx.get("https://api.polygon.io/v3/snapshot/options/AAPL").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(500),
            httpx.Response(200, json=_payload()),
        ]
    )
    out = PolygonOptionsClient(api_key="k", base_url="https://api.polygon.io").get_chain("AAPL")
    assert route.call_count == 3
    assert out == []


@respx.mock
def test_skips_malformed_entries_without_crashing() -> None:
    payload = _payload(
        {"details": "not-a-dict"},
        {"details": {"ticker": "BOGUS_NOT_OCC"}},
        _entry(
            ticker="O:AAPL240517C00170000",
            contract_type="call",
            strike=170.0,
            expiration="2024-05-17",
        ),
    )
    respx.get("https://api.polygon.io/v3/snapshot/options/AAPL").mock(
        return_value=httpx.Response(200, json=payload)
    )
    out = PolygonOptionsClient(api_key="k", base_url="https://api.polygon.io").get_chain("AAPL")
    assert len(out) == 1
    assert out[0].symbol == "AAPL"


@respx.mock
def test_handles_missing_quote_and_greeks() -> None:
    payload = _payload(
        {
            "details": {
                "ticker": "O:AAPL240517C00170000",
                "contract_type": "call",
                "strike_price": 170.0,
                "expiration_date": "2024-05-17",
            },
            "open_interest": 100,
        }
    )
    respx.get("https://api.polygon.io/v3/snapshot/options/AAPL").mock(
        return_value=httpx.Response(200, json=payload)
    )
    out = PolygonOptionsClient(api_key="k", base_url="https://api.polygon.io").get_chain("AAPL")
    assert len(out) == 1
    record = out[0]
    assert record.bid is None
    assert record.ask is None
    assert record.last is None
    assert record.volume is None
    assert record.open_interest == 100
    assert record.delta is None
    assert record.iv is None


@respx.mock
def test_handles_empty_results() -> None:
    respx.get("https://api.polygon.io/v3/snapshot/options/AAPL").mock(
        return_value=httpx.Response(200, json={"status": "OK", "results": []})
    )
    out = PolygonOptionsClient(api_key="k", base_url="https://api.polygon.io").get_chain("AAPL")
    assert out == []


@respx.mock
def test_handles_missing_results_key() -> None:
    respx.get("https://api.polygon.io/v3/snapshot/options/AAPL").mock(
        return_value=httpx.Response(200, json={"status": "OK"})
    )
    out = PolygonOptionsClient(api_key="k", base_url="https://api.polygon.io").get_chain("AAPL")
    assert out == []


def test_missing_api_key_raises() -> None:
    with pytest.raises(PolygonError):
        PolygonOptionsClient(api_key="")


@respx.mock
def test_unparsable_ticker_skipped() -> None:
    payload = _payload(
        {
            "details": {
                "ticker": "O:NOTAVALIDOCC",
                "contract_type": "call",
                "strike_price": 100.0,
                "expiration_date": "2024-05-17",
            },
        }
    )
    respx.get("https://api.polygon.io/v3/snapshot/options/AAPL").mock(
        return_value=httpx.Response(200, json=payload)
    )
    out = PolygonOptionsClient(api_key="k", base_url="https://api.polygon.io").get_chain("AAPL")
    assert out == []
