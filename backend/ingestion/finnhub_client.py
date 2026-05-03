"""Finnhub HTTP client wrapper for the earnings calendar.

Mirrors the patterns in ``ingestion/alpaca_client.py``: tenacity retry on
transient network errors, structured logging, an injectable underlying client
for tests, and a normalized record type.

Finnhub free tier limits: 60 calls/min, US equities only. Without an API key
configured, callers should skip earnings ingestion (see ``ingestion/earnings``).

A built-in sliding-window rate limiter (``_RateLimiter``) caps outbound calls
to ``settings.finnhub_rate_limit_per_min`` (default 55, just under the free-
tier ceiling) so a tight per-symbol loop can't burst past the limit. The
limiter is process-local — fine for our single-process scheduler but worth
remembering if we ever fan out across workers.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import date

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


class _RateLimiter:
    """Thread-safe sliding-window limiter: at most ``max_calls`` per ``window_s``."""

    def __init__(self, max_calls: int, window_s: float = 60.0) -> None:
        self._max = max(1, int(max_calls))
        self._window = float(window_s)
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a slot is available, then record the call timestamp."""
        while True:
            with self._lock:
                now = time.monotonic()
                cutoff = now - self._window
                while self._calls and self._calls[0] <= cutoff:
                    self._calls.popleft()
                if len(self._calls) < self._max:
                    self._calls.append(now)
                    return
                wait_for = self._window - (now - self._calls[0])
            if wait_for > 0:
                log.info("finnhub.rate_limit.sleep", seconds=round(wait_for, 2))
                time.sleep(wait_for)


@dataclass(frozen=True, slots=True)
class EarningsRecord:
    """Normalized earnings calendar entry."""

    symbol: str
    earnings_date: date
    time_of_day: str | None


@dataclass(frozen=True, slots=True)
class CompanyProfile:
    """Subset of Finnhub /stock/profile2 we persist on the tickers table.

    ``market_cap`` is in absolute USD (Finnhub returns millions; we expand
    on normalize). ``sector`` is sourced from ``finnhubIndustry`` — Finnhub
    doesn't expose a separate sector vs. industry split on the free tier.
    """

    symbol: str
    name: str | None
    sector: str | None
    market_cap: float | None


class FinnhubError(RuntimeError):
    """Raised when Finnhub returns a non-retryable error."""


_RETRYABLE_EXCEPTIONS = (
    httpx.TransportError,
    httpx.TimeoutException,
    httpx.HTTPStatusError,
)


class FinnhubClient:
    """Sync HTTP client for Finnhub's earnings calendar endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        client: httpx.Client | None = None,
        rate_limit_per_min: int | None = None,
    ) -> None:
        settings = get_settings()
        key = api_key if api_key is not None else settings.finnhub_api_key
        if not key:
            raise FinnhubError("Finnhub API key missing — set FINNHUB_API_KEY")
        self._key = key
        self._base = (base_url or settings.finnhub_base_url).rstrip("/")
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS)
        limit = (
            rate_limit_per_min
            if rate_limit_per_min is not None
            else settings.finnhub_rate_limit_per_min
        )
        self._limiter = _RateLimiter(max_calls=limit, window_s=60.0)

    @retry(
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        reraise=True,
    )
    def get_earnings_calendar(
        self,
        *,
        from_date: date,
        to_date: date,
        symbol: str | None = None,
    ) -> list[EarningsRecord]:
        """Fetch earnings between ``from_date`` and ``to_date`` (inclusive)."""
        params: dict[str, str] = {
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "token": self._key,
        }
        if symbol:
            params["symbol"] = symbol

        log.info(
            "finnhub.get_earnings_calendar",
            from_date=str(from_date),
            to_date=str(to_date),
            symbol=symbol,
        )
        self._limiter.acquire()
        response = self._client.get(f"{self._base}/calendar/earnings", params=params)
        response.raise_for_status()
        return _normalize(response.json())

    @retry(
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        reraise=True,
    )
    def get_company_profile(self, symbol: str) -> CompanyProfile | None:
        """Fetch company metadata for ``symbol``. Returns ``None`` if Finnhub
        has no profile (delisted, non-US, ETF on free tier)."""
        params = {"symbol": symbol, "token": self._key}
        log.info("finnhub.get_company_profile", symbol=symbol)
        self._limiter.acquire()
        response = self._client.get(f"{self._base}/stock/profile2", params=params)
        response.raise_for_status()
        return _normalize_profile(symbol, response.json())


def _normalize_profile(symbol: str, payload: object) -> CompanyProfile | None:
    if not isinstance(payload, dict) or not payload:
        return None
    name = payload.get("name")
    industry = payload.get("finnhubIndustry")
    raw_cap = payload.get("marketCapitalization")
    market_cap: float | None = None
    if isinstance(raw_cap, (int, float)) and raw_cap > 0:
        market_cap = float(raw_cap) * 1_000_000  # Finnhub returns millions of USD
    return CompanyProfile(
        symbol=symbol,
        name=name if isinstance(name, str) and name else None,
        sector=industry if isinstance(industry, str) and industry else None,
        market_cap=market_cap,
    )


def _normalize(payload: object) -> list[EarningsRecord]:
    if not isinstance(payload, dict):
        raise FinnhubError(f"unexpected payload shape: {type(payload)!r}")

    raw = payload.get("earningsCalendar")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise FinnhubError(f"unexpected earningsCalendar shape: {type(raw)!r}")

    out: list[EarningsRecord] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        symbol = entry.get("symbol")
        date_str = entry.get("date")
        if not isinstance(symbol, str) or not isinstance(date_str, str):
            continue
        try:
            earnings_date = date.fromisoformat(date_str)
        except ValueError:
            log.warning("finnhub.bad_date_skipped", symbol=symbol, raw=date_str)
            continue
        out.append(
            EarningsRecord(
                symbol=symbol,
                earnings_date=earnings_date,
                time_of_day=_normalize_hour(entry.get("hour")),
            )
        )
    return out


def _normalize_hour(value: object) -> str | None:
    """Finnhub returns "bmo", "amc", or empty string. Normalize to canonical."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    if cleaned == "bmo":
        return "BMO"
    if cleaned == "amc":
        return "AMC"
    if cleaned == "":
        return None
    return "unknown"
