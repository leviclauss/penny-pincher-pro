"""Polygon.io HTTP client for option chain snapshots.

Polygon's ``/v3/snapshot/options/{underlying}`` returns a chain row per
contract with bid/ask, last trade, day aggregates (including ``volume``),
top-level ``open_interest``, greeks, and ``implied_volatility``. That fills
the OI + volume gap left by Alpaca's free ``indicative`` feed and unblocks
the screener's liquidity filters.

Mirrors the patterns in ``ingestion/finnhub_client.py`` and
``ingestion/options_client.py``: tenacity retry, sliding-window rate
limiter, an injectable ``httpx.Client`` for tests, and the same normalized
``OptionSnapshotRecord`` returned by the Alpaca client so callers (notably
``ingestion.options.fetch_chains`` via the ``ChainSource`` Protocol) don't
care which vendor is in use.

Auth uses ``Authorization: Bearer <key>`` so pagination URLs don't need
key manipulation. Polygon's ``next_url`` field is fully formed but omits
the API key; the bearer header carries it cleanly across follow-ups.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections import deque
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
from ingestion.options_client import OCCParseError, OptionSnapshotRecord, parse_occ_symbol

log = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_PAGE_LIMIT = 250  # Polygon max
MAX_PAGES = 20  # Hard ceiling so a runaway cursor can't loop forever.


class _RateLimiter:
    """Thread-safe sliding-window limiter: at most ``max_calls`` per ``window_s``."""

    def __init__(self, max_calls: int, window_s: float = 60.0) -> None:
        self._max = max(1, int(max_calls))
        self._window = float(window_s)
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
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
                log.info("polygon.rate_limit.sleep", seconds=round(wait_for, 2))
                time.sleep(wait_for)


class PolygonError(RuntimeError):
    """Raised when Polygon returns a non-retryable error."""


_RETRYABLE_EXCEPTIONS = (
    httpx.TransportError,
    httpx.TimeoutException,
    httpx.HTTPStatusError,
)


class PolygonOptionsClient:
    """Sync HTTP client for Polygon's option chain snapshot endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        client: httpx.Client | None = None,
        rate_limit_per_min: int | None = None,
    ) -> None:
        settings = get_settings()
        key = api_key if api_key is not None else settings.polygon_api_key
        if not key:
            raise PolygonError("Polygon API key missing — set POLYGON_API_KEY")
        self._key = key
        self._base = (base_url or settings.polygon_base_url).rstrip("/")
        self._client = client or httpx.Client(
            timeout=DEFAULT_TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {key}"},
        )
        limit = (
            rate_limit_per_min
            if rate_limit_per_min is not None
            else settings.polygon_rate_limit_per_min
        )
        self._limiter = _RateLimiter(max_calls=limit, window_s=60.0)

    @retry(
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        reraise=True,
    )
    def get_chain(
        self,
        underlying: str,
        *,
        expiration_gte: date | None = None,
        expiration_lte: date | None = None,
        strike_gte: float | None = None,
        strike_lte: float | None = None,
    ) -> list[OptionSnapshotRecord]:
        """Fetch the full option chain for ``underlying``, paginating as needed.

        Server-side filters are applied via Polygon's ``strike_price`` and
        ``expiration_date`` range params. Result shape matches the Alpaca
        client so ``ChainSource`` callers don't branch on vendor.
        """
        log.info(
            "polygon.get_option_chain",
            underlying=underlying,
            expiration_gte=str(expiration_gte) if expiration_gte else None,
            expiration_lte=str(expiration_lte) if expiration_lte else None,
            strike_gte=strike_gte,
            strike_lte=strike_lte,
        )

        params: dict[str, str] = {"limit": str(DEFAULT_PAGE_LIMIT)}
        if expiration_gte is not None:
            params["expiration_date.gte"] = expiration_gte.isoformat()
        if expiration_lte is not None:
            params["expiration_date.lte"] = expiration_lte.isoformat()
        if strike_gte is not None:
            params["strike_price.gte"] = f"{strike_gte:.4f}"
        if strike_lte is not None:
            params["strike_price.lte"] = f"{strike_lte:.4f}"

        url = f"{self._base}/v3/snapshot/options/{underlying.upper()}"
        records: list[OptionSnapshotRecord] = []
        next_url: str | None = url
        next_params: dict[str, str] | None = params

        for _ in range(MAX_PAGES):
            if next_url is None:
                break
            self._limiter.acquire()
            response = self._client.get(next_url, params=next_params)
            response.raise_for_status()
            payload = response.json()
            records.extend(_normalize_chain(payload))
            next_url = _extract_next_url(payload)
            # Polygon's next_url is fully formed; strip params on follow-up
            # so we don't double-encode.
            next_params = None
        else:
            log.warning("polygon.pagination_capped", underlying=underlying, max_pages=MAX_PAGES)

        return records


def _extract_next_url(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    nxt = payload.get("next_url")
    return nxt if isinstance(nxt, str) and nxt else None


def _normalize_chain(payload: object) -> list[OptionSnapshotRecord]:
    if not isinstance(payload, dict):
        raise PolygonError(f"unexpected payload shape: {type(payload)!r}")
    results = payload.get("results")
    if results is None:
        return []
    if not isinstance(results, list):
        raise PolygonError(f"unexpected results shape: {type(results)!r}")

    records: list[OptionSnapshotRecord] = []
    for entry in results:
        if not isinstance(entry, dict):
            continue
        record = _normalize_entry(entry)
        if record is not None:
            records.append(record)
    return records


def _normalize_entry(entry: dict[str, object]) -> OptionSnapshotRecord | None:
    details = entry.get("details")
    if not isinstance(details, dict):
        return None

    occ_ticker = details.get("ticker")
    if not isinstance(occ_ticker, str):
        return None
    # Polygon prefixes option tickers with "O:".
    occ = occ_ticker[2:] if occ_ticker.startswith("O:") else occ_ticker
    try:
        root, expiration, option_type, strike = parse_occ_symbol(occ)
    except OCCParseError:
        log.warning("polygon.unparsable_symbol", symbol=occ_ticker)
        return None

    # Prefer details.* over derived OCC fields when present — Polygon's
    # canonical values are authoritative if they disagree.
    contract_type = details.get("contract_type")
    if isinstance(contract_type, str) and contract_type in ("call", "put"):
        option_type = contract_type
    raw_strike = details.get("strike_price")
    if isinstance(raw_strike, (int, float)):
        strike = float(raw_strike)
    raw_exp = details.get("expiration_date")
    if isinstance(raw_exp, str):
        with contextlib.suppress(ValueError):
            expiration = date.fromisoformat(raw_exp)

    quote = entry.get("last_quote") if isinstance(entry.get("last_quote"), dict) else None
    trade = entry.get("last_trade") if isinstance(entry.get("last_trade"), dict) else None
    greeks = entry.get("greeks") if isinstance(entry.get("greeks"), dict) else None
    day = entry.get("day") if isinstance(entry.get("day"), dict) else None

    return OptionSnapshotRecord(
        symbol=root,
        expiration=expiration,
        strike=strike,
        option_type=option_type,
        bid=_get_float(quote, "bid"),
        ask=_get_float(quote, "ask"),
        last=_get_float(trade, "price"),
        volume=_get_int(day, "volume"),
        open_interest=_get_int(entry, "open_interest"),
        delta=_get_float(greeks, "delta"),
        gamma=_get_float(greeks, "gamma"),
        theta=_get_float(greeks, "theta"),
        vega=_get_float(greeks, "vega"),
        iv=_get_float(entry, "implied_volatility"),
    )


def _get_float(obj: object, key: str) -> float | None:
    if not isinstance(obj, dict):
        return None
    value = obj.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _get_int(obj: object, key: str) -> int | None:
    if not isinstance(obj, dict):
        return None
    value = obj.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None
