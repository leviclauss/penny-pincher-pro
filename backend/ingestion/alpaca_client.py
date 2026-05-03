"""Thin wrapper around ``alpaca-py``'s ``StockHistoricalDataClient``.

Adds retry/backoff on transient errors, structured logging, and a normalized
return type. Higher layers (``ingestion.bars``) handle batching and persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from alpaca.data.enums import Adjustment
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
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


@dataclass(frozen=True, slots=True)
class QuoteRecord:
    """Latest NBBO quote — used by the intraday alert pulse for freshness + price."""

    symbol: str
    timestamp: datetime
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        return self.ask if self.ask > 0 else self.bid


class _BarsClient(Protocol):
    def get_stock_bars(self, request: StockBarsRequest) -> object: ...

    def get_stock_latest_quote(self, request_params: StockLatestQuoteRequest) -> object: ...


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
            key = api_key if api_key is not None else settings.alpaca_api_key
            secret = api_secret if api_secret is not None else settings.alpaca_api_secret
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
            adjustment="split",
        )
        # Alpaca treats the end date as exclusive for daily bars, so add 1 day
        # to preserve the inclusive-end contract the rest of the codebase expects.
        end_exclusive = (
            end + timedelta(days=1) if isinstance(end, date) and not isinstance(end, datetime)
            else end
        )
        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,
            start=start,
            end=end_exclusive,
            feed=self._feed,
            adjustment=Adjustment.SPLIT,
        )
        response = self._client.get_stock_bars(request)
        return _normalize_bar_response(response)

    @retry(
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        reraise=True,
    )
    def get_latest_quotes(self, symbols: list[str]) -> dict[str, QuoteRecord]:
        """Fetch the latest NBBO quote per symbol — used by the intraday pulse.

        Symbols absent from the response (no quote in the requested feed)
        are absent from the returned dict. The caller decides how to handle
        a missing or stale symbol.
        """
        if not symbols:
            return {}
        log.info(
            "alpaca.get_latest_quotes",
            symbol_count=len(symbols),
            feed=self._feed,
        )
        request = StockLatestQuoteRequest(symbol_or_symbols=symbols, feed=self._feed)
        response = self._client.get_stock_latest_quote(request)
        return _normalize_quote_response(response)


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


def _normalize_quote_response(response: object) -> dict[str, QuoteRecord]:
    """Convert alpaca-py's ``{symbol: Quote}`` mapping into ``QuoteRecord``s."""
    if isinstance(response, dict):
        items = response.items()
    else:
        raw = getattr(response, "data", None)
        if isinstance(raw, dict):
            items = raw.items()
        else:
            raise AlpacaDataError(f"unexpected quote response shape: {type(response)!r}")

    out: dict[str, QuoteRecord] = {}
    for symbol, quote in items:
        ts = getattr(quote, "timestamp", None)
        if ts is None:
            continue
        bid = getattr(quote, "bid_price", None)
        ask = getattr(quote, "ask_price", None)
        if bid is None and ask is None:
            continue
        out[symbol] = QuoteRecord(
            symbol=symbol,
            timestamp=ts,
            bid=float(bid) if bid is not None else 0.0,
            ask=float(ask) if ask is not None else 0.0,
        )
    return out
