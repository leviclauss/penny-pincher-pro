"""Thin wrapper around ``alpaca-py``'s ``StockHistoricalDataClient``.

Adds retry/backoff on transient errors, structured logging, and a normalized
return type. Higher layers (``ingestion.bars``) handle batching and persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Protocol

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.config import get_settings
from core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    pass

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BarRecord:
    """Normalized daily bar — what callers see, regardless of SDK version."""

    symbol: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


class _BarsClient(Protocol):
    def get_stock_bars(self, request: StockBarsRequest) -> object: ...


_RETRYABLE_EXCEPTIONS = (ConnectionError, TimeoutError, OSError)


class AlpacaDataError(RuntimeError):
    """Raised when Alpaca returns a non-retryable error."""


class AlpacaClient:
    """Sync wrapper around the Alpaca historical data SDK.

    Construct once per process; the underlying client is thread-safe enough
    for our single-process ingestion pipeline.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        feed: str | None = None,
        *,
        client: _BarsClient | None = None,
    ) -> None:
        settings = get_settings()
        self._feed = feed or settings.alpaca_data_feed
        if client is not None:
            self._client: _BarsClient = client
        else:
            key = api_key or settings.alpaca_api_key
            secret = api_secret or settings.alpaca_api_secret
            if not key or not secret:
                raise AlpacaDataError(
                    "Alpaca credentials missing — set ALPACA_API_KEY / ALPACA_API_SECRET"
                )
            self._client = StockHistoricalDataClient(key, secret)

    @retry(
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        reraise=True,
    )
    def get_daily_bars(
        self,
        symbols: list[str],
        start: date | datetime,
        end: date | datetime,
    ) -> dict[str, list[BarRecord]]:
        """Fetch daily OHLCV bars for ``symbols`` over ``[start, end]``.

        Returns a dict keyed by symbol. Symbols with no data are absent from
        the result (caller decides how to handle a missing symbol).
        """
        if not symbols:
            return {}
        log.info(
            "alpaca.get_daily_bars",
            symbol_count=len(symbols),
            start=str(start),
            end=str(end),
            feed=self._feed,
        )
        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            feed=self._feed,
        )
        response = self._client.get_stock_bars(request)
        return _normalize_bar_response(response)


def _normalize_bar_response(response: object) -> dict[str, list[BarRecord]]:
    """Convert an alpaca-py ``BarSet`` into a dict of normalized records."""
    raw = getattr(response, "data", response)
    if not isinstance(raw, dict):
        raise AlpacaDataError(f"unexpected response shape: {type(response)!r}")

    out: dict[str, list[BarRecord]] = {}
    for symbol, bars in raw.items():
        records: list[BarRecord] = []
        for bar in bars:
            ts = getattr(bar, "timestamp", None)
            if ts is None:
                continue
            bar_date = ts.date() if hasattr(ts, "date") else ts
            records.append(
                BarRecord(
                    symbol=symbol,
                    date=bar_date,
                    open=float(bar.open),
                    high=float(bar.high),
                    low=float(bar.low),
                    close=float(bar.close),
                    volume=int(bar.volume),
                )
            )
        if records:
            out[symbol] = records
    return out
