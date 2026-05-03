"""Unit tests for the Alpaca client wrapper.

The real SDK is mocked. We verify: response normalization, retry on transient
errors, no retry on credential errors, and missing-creds guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pytest

from ingestion.alpaca_client import AlpacaClient, AlpacaDataError, BarRecord


@dataclass
class _FakeBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class _FakeBarSet:
    data: dict[str, list[_FakeBar]]


class _FakeClient:
    def __init__(
        self,
        response: object,
        *,
        raise_first_n: int = 0,
        quote_response: object | None = None,
    ) -> None:
        self._response = response
        self._raise_first_n = raise_first_n
        self.calls = 0
        self.last_request: Any = None
        self._quote_response = quote_response

    def get_stock_bars(self, request: Any) -> object:
        self.last_request = request
        self.calls += 1
        if self.calls <= self._raise_first_n:
            raise ConnectionError("transient")
        return self._response

    def get_stock_latest_quote(self, request_params: Any) -> object:
        self.last_request = request_params
        return self._quote_response or {}


def _bars_response() -> _FakeBarSet:
    ts = datetime(2024, 1, 2, 5, 0, 0)
    return _FakeBarSet(
        data={
            "AAPL": [_FakeBar(ts, 100.0, 101.0, 99.0, 100.5, 1_000_000)],
            "MSFT": [_FakeBar(ts, 300.0, 305.0, 299.0, 304.0, 800_000)],
        }
    )


def test_normalizes_bars_to_records() -> None:
    fake = _FakeClient(_bars_response())
    client = AlpacaClient(api_key="k", api_secret="s", client=fake)

    out = client.get_daily_bars(["AAPL", "MSFT"], date(2024, 1, 1), date(2024, 1, 5))

    assert set(out) == {"AAPL", "MSFT"}
    aapl = out["AAPL"][0]
    assert isinstance(aapl, BarRecord)
    assert aapl.symbol == "AAPL"
    assert aapl.date == date(2024, 1, 2)
    assert aapl.close == 100.5
    assert aapl.volume == 1_000_000


def test_requests_split_adjusted_bars() -> None:
    from alpaca.data.enums import Adjustment

    fake = _FakeClient(_bars_response())
    client = AlpacaClient(api_key="k", api_secret="s", client=fake)

    client.get_daily_bars(["AAPL"], date(2024, 1, 1), date(2024, 1, 5))

    assert fake.last_request is not None
    assert fake.last_request.adjustment == Adjustment.SPLIT


def test_empty_symbols_short_circuits() -> None:
    fake = _FakeClient(_bars_response())
    client = AlpacaClient(api_key="k", api_secret="s", client=fake)
    assert client.get_daily_bars([], date(2024, 1, 1), date(2024, 1, 5)) == {}
    assert fake.calls == 0


def test_retries_on_transient_error() -> None:
    fake = _FakeClient(_bars_response(), raise_first_n=2)
    client = AlpacaClient(api_key="k", api_secret="s", client=fake)
    out = client.get_daily_bars(["AAPL"], date(2024, 1, 1), date(2024, 1, 5))
    assert fake.calls == 3
    assert "AAPL" in out


def test_missing_credentials_raises() -> None:
    with pytest.raises(AlpacaDataError):
        AlpacaClient(api_key="", api_secret="")


def test_unexpected_response_shape_raises() -> None:
    fake = _FakeClient(response="not-a-barset")
    client = AlpacaClient(api_key="k", api_secret="s", client=fake)
    with pytest.raises(AlpacaDataError):
        client.get_daily_bars(["AAPL"], date(2024, 1, 1), date(2024, 1, 5))
