"""Unit tests for the Alpaca options client wrapper.

The real SDK is mocked. We verify: OCC parsing, response normalization,
unparsable symbols are logged + skipped (not crashed), retry on transient
errors, and missing-creds guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest

from ingestion.options_client import (
    AlpacaOptionsClient,
    AlpacaOptionsError,
    OCCParseError,
    parse_occ_symbol,
)


@dataclass
class _Quote:
    bid_price: float | None = None
    ask_price: float | None = None


@dataclass
class _Trade:
    price: float | None = None


@dataclass
class _Greeks:
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None


@dataclass
class _Snapshot:
    latest_quote: _Quote | None = None
    latest_trade: _Trade | None = None
    greeks: _Greeks | None = None
    implied_volatility: float | None = None


class _FakeChainClient:
    def __init__(self, response: object, *, raise_first_n: int = 0) -> None:
        self._response = response
        self._raise_first_n = raise_first_n
        self.calls = 0

    def get_option_chain(self, request_params: Any) -> object:
        _ = request_params
        self.calls += 1
        if self.calls <= self._raise_first_n:
            raise ConnectionError("transient")
        return self._response


def _chain_response() -> dict[str, _Snapshot]:
    return {
        "AAPL240517C00170000": _Snapshot(
            latest_quote=_Quote(bid_price=2.10, ask_price=2.15),
            latest_trade=_Trade(price=2.12),
            greeks=_Greeks(delta=0.55, gamma=0.04, theta=-0.05, vega=0.12),
            implied_volatility=0.28,
        ),
        "AAPL240517P00170000": _Snapshot(
            latest_quote=_Quote(bid_price=1.95, ask_price=2.00),
            greeks=_Greeks(delta=-0.45, gamma=0.04, theta=-0.05, vega=0.12),
            implied_volatility=0.30,
        ),
    }


def test_parse_occ_symbol_call_and_put() -> None:
    root, exp, kind, strike = parse_occ_symbol("AAPL240517C00170000")
    assert root == "AAPL"
    assert exp == date(2024, 5, 17)
    assert kind == "call"
    assert strike == 170.0

    root, _, kind, strike = parse_occ_symbol("SPY251219P00425500")
    assert root == "SPY"
    assert kind == "put"
    assert strike == 425.5


def test_parse_occ_symbol_rejects_garbage() -> None:
    with pytest.raises(OCCParseError):
        parse_occ_symbol("notavalidsymbol")
    with pytest.raises(OCCParseError):
        parse_occ_symbol("AAPL999999X00170000")


def test_normalizes_chain_response() -> None:
    fake = _FakeChainClient(_chain_response())
    client = AlpacaOptionsClient(api_key="k", api_secret="s", client=fake)

    out = client.get_chain("AAPL")

    assert len(out) == 2
    by_type = {r.option_type: r for r in out}
    assert "call" in by_type and "put" in by_type
    call = by_type["call"]
    assert call.symbol == "AAPL"
    assert call.strike == 170.0
    assert call.expiration == date(2024, 5, 17)
    assert call.bid == 2.10
    assert call.ask == 2.15
    assert call.last == 2.12
    assert call.delta == 0.55
    assert call.iv == 0.28
    assert call.volume is None
    assert call.open_interest is None


def test_skips_unparsable_symbols_without_crashing() -> None:
    response = _chain_response()
    response["GARBAGE"] = _Snapshot()
    fake = _FakeChainClient(response)
    client = AlpacaOptionsClient(api_key="k", api_secret="s", client=fake)

    out = client.get_chain("AAPL")
    assert len(out) == 2


def test_handles_missing_quote_and_greeks() -> None:
    response = {"AAPL240517C00170000": _Snapshot()}
    fake = _FakeChainClient(response)
    client = AlpacaOptionsClient(api_key="k", api_secret="s", client=fake)

    out = client.get_chain("AAPL")
    record = out[0]
    assert record.bid is None
    assert record.ask is None
    assert record.delta is None
    assert record.iv is None


def test_retries_on_transient_error() -> None:
    fake = _FakeChainClient(_chain_response(), raise_first_n=2)
    client = AlpacaOptionsClient(api_key="k", api_secret="s", client=fake)
    out = client.get_chain("AAPL")
    assert fake.calls == 3
    assert len(out) == 2


def test_missing_credentials_raises() -> None:
    with pytest.raises(AlpacaOptionsError):
        AlpacaOptionsClient(api_key="", api_secret="")


def test_unexpected_response_shape_raises() -> None:
    fake = _FakeChainClient(response="not-a-dict")
    client = AlpacaOptionsClient(api_key="k", api_secret="s", client=fake)
    with pytest.raises(AlpacaOptionsError):
        client.get_chain("AAPL")
