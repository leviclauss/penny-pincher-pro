"""Yahoo Finance v8 chart API client (no auth).

Used for index data Alpaca doesn't reliably expose: ``^VIX``, ``^VIX9D``.
Endpoint: ``GET /v8/finance/chart/{symbol}?period1=&period2=&interval=1d``.
Returns JSON with parallel arrays of timestamps and OHLCV — we collapse to
``(date, close)`` since that's all the macro layer cares about.

Yahoo's API is unauthenticated and best-effort: occasional shape changes are
expected. We tolerate missing days (some symbols return null closes for
holidays) by skipping them rather than failing the whole fetch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.config import get_settings
from core.logging import get_logger

log = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 10.0
USER_AGENT = "Mozilla/5.0 (compatible; penny-pincher-pro/0.1)"


@dataclass(frozen=True, slots=True)
class IndexBarRecord:
    """One daily close for an index symbol."""

    symbol: str
    date: date
    close: float


class YahooError(RuntimeError):
    """Raised when Yahoo returns a non-retryable shape."""


_RETRYABLE_EXCEPTIONS = (
    httpx.TransportError,
    httpx.TimeoutException,
    httpx.HTTPStatusError,
)


class YahooClient:
    """Sync HTTP client for the Yahoo Finance v8 chart endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        settings = get_settings()
        self._base = (base_url or settings.yahoo_base_url).rstrip("/")
        self._client = client or httpx.Client(
            timeout=DEFAULT_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        )

    @retry(
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        reraise=True,
    )
    def get_index_history(
        self,
        symbol: str,
        *,
        days_back: int,
        as_of: date | None = None,
    ) -> list[IndexBarRecord]:
        """Fetch daily closes for ``symbol`` over the past ``days_back`` days."""
        end = as_of or datetime.now(UTC).date()
        start = end - timedelta(days=days_back)
        period1 = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())
        period2 = int(datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp()) + 86_400

        log.info("yahoo.get_index_history", symbol=symbol, start=str(start), end=str(end))
        response = self._client.get(
            f"{self._base}/v8/finance/chart/{symbol}",
            params={"period1": str(period1), "period2": str(period2), "interval": "1d"},
        )
        response.raise_for_status()
        return _normalize(symbol, response.json())


def _normalize(symbol: str, payload: object) -> list[IndexBarRecord]:
    if not isinstance(payload, dict):
        raise YahooError(f"unexpected payload shape for {symbol}: {type(payload)!r}")

    chart = payload.get("chart")
    if not isinstance(chart, dict):
        raise YahooError(f"missing 'chart' for {symbol}")
    results = chart.get("result")
    if not isinstance(results, list) or not results:
        return []
    result = results[0]
    if not isinstance(result, dict):
        return []

    timestamps = result.get("timestamp")
    indicators = result.get("indicators", {})
    if not isinstance(timestamps, list) or not isinstance(indicators, dict):
        return []
    quotes = indicators.get("quote")
    if not isinstance(quotes, list) or not quotes:
        return []
    closes = quotes[0].get("close") if isinstance(quotes[0], dict) else None
    if not isinstance(closes, list):
        return []

    out: list[IndexBarRecord] = []
    for ts, close in zip(timestamps, closes, strict=False):
        if close is None or not isinstance(ts, int | float):
            continue
        out.append(
            IndexBarRecord(
                symbol=symbol,
                date=datetime.fromtimestamp(int(ts), UTC).date(),
                close=float(close),
            )
        )
    return out
