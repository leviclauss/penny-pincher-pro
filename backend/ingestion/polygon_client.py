"""Polygon.io HTTP client for option chain snapshots and historical chains.

Three endpoints in play:

- ``/v3/snapshot/options/{underlying}`` — current chain with bid/ask, day
  aggregates (volume), top-level ``open_interest``, greeks, IV. Fills the
  OI + volume gap left by Alpaca's free ``indicative`` feed.
- ``/v3/reference/options/contracts`` — enumerate the contracts that
  existed for an underlying on a historical date (``as_of`` param,
  ``expired=true`` to include matured ones). The Phase 2 backfill uses
  this to discover the contract universe for a given window.
- ``/v2/aggs/ticker/O:{occ}/range/1/day/{from}/{to}`` — daily OHLCV per
  contract over a date range. The strategy backtest reads ``close`` as
  the historical mid (Polygon Developer doesn't expose historical bid/ask
  at this tier, and daily resolution doesn't need it).

Mirrors the patterns in ``ingestion/finnhub_client.py`` and
``ingestion/options_client.py``: tenacity retry, sliding-window rate
limiter, an injectable ``httpx.Client`` for tests, normalized record
types decoupled from SDK shape changes.

Auth uses ``Authorization: Bearer <key>`` so pagination URLs don't need
key manipulation. Polygon's ``next_url`` field is fully formed but omits
the API key; the bearer header carries it cleanly across follow-ups.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, date, datetime

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


@dataclass(frozen=True, slots=True)
class OptionContractRef:
    """Reference metadata for one option contract that existed on a date."""

    occ: str
    underlying: str
    expiration: date
    strike: float
    option_type: str  # "call" or "put"


@dataclass(frozen=True, slots=True)
class OptionDailyAgg:
    """One daily OHLCV bar for a single option contract."""

    occ: str
    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None


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

    def list_contracts(
        self,
        underlying: str,
        *,
        as_of: date | None = None,
        expiration_gte: date | None = None,
        expiration_lte: date | None = None,
        strike_gte: float | None = None,
        strike_lte: float | None = None,
        include_expired: bool = True,
    ) -> list[OptionContractRef]:
        """Enumerate option contracts for ``underlying`` (paginated).

        ``as_of`` filters to contracts that existed on that date.
        ``include_expired=True`` (default) is required to discover contracts
        that have already matured — without it Polygon only returns the
        currently-live chain, which is useless for historical backfill.

        Polygon's ``expired`` query param is exclusive: ``expired=true``
        returns ONLY already-expired contracts and ``expired=false`` returns
        ONLY live ones. To honor ``include_expired=True`` we issue both
        calls and merge by OCC ticker.
        """
        log.info(
            "polygon.list_contracts",
            underlying=underlying,
            as_of=str(as_of) if as_of else None,
            expiration_gte=str(expiration_gte) if expiration_gte else None,
            expiration_lte=str(expiration_lte) if expiration_lte else None,
            include_expired=include_expired,
        )
        flags = (False, True) if include_expired else (False,)
        seen: dict[str, OptionContractRef] = {}
        for expired in flags:
            for c in self._list_contracts_one(
                underlying,
                as_of=as_of,
                expiration_gte=expiration_gte,
                expiration_lte=expiration_lte,
                strike_gte=strike_gte,
                strike_lte=strike_lte,
                expired=expired,
            ):
                seen[c.occ] = c
        return list(seen.values())

    @retry(
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        reraise=True,
    )
    def _list_contracts_one(
        self,
        underlying: str,
        *,
        as_of: date | None,
        expiration_gte: date | None,
        expiration_lte: date | None,
        strike_gte: float | None,
        strike_lte: float | None,
        expired: bool,
    ) -> list[OptionContractRef]:
        params: dict[str, str] = {
            "underlying_ticker": underlying.upper(),
            "limit": str(DEFAULT_PAGE_LIMIT),
            "expired": "true" if expired else "false",
        }
        if as_of is not None:
            params["as_of"] = as_of.isoformat()
        if expiration_gte is not None:
            params["expiration_date.gte"] = expiration_gte.isoformat()
        if expiration_lte is not None:
            params["expiration_date.lte"] = expiration_lte.isoformat()
        if strike_gte is not None:
            params["strike_price.gte"] = f"{strike_gte:.4f}"
        if strike_lte is not None:
            params["strike_price.lte"] = f"{strike_lte:.4f}"

        url = f"{self._base}/v3/reference/options/contracts"
        contracts: list[OptionContractRef] = []
        next_url: str | None = url
        next_params: dict[str, str] | None = params

        for _ in range(MAX_PAGES):
            if next_url is None:
                break
            self._limiter.acquire()
            response = self._client.get(next_url, params=next_params)
            response.raise_for_status()
            payload = response.json()
            contracts.extend(_normalize_contracts(payload))
            next_url = _extract_next_url(payload)
            next_params = None
        else:
            log.warning(
                "polygon.contracts_pagination_capped",
                underlying=underlying,
                max_pages=MAX_PAGES,
                expired=expired,
            )

        return contracts

    @retry(
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        reraise=True,
    )
    def get_contract_aggs(
        self,
        occ: str,
        *,
        from_date: date,
        to_date: date,
        adjusted: bool = True,
    ) -> list[OptionDailyAgg]:
        """Daily OHLCV bars for one contract over ``[from_date, to_date]``.

        Polygon returns one bar per trading day the contract had volume.
        Days with no trading are simply absent from the response — callers
        must not assume every calendar day is covered.
        """
        log.info(
            "polygon.get_contract_aggs",
            occ=occ,
            from_date=str(from_date),
            to_date=str(to_date),
        )
        ticker = occ if occ.startswith("O:") else f"O:{occ}"
        url = (
            f"{self._base}/v2/aggs/ticker/{ticker}"
            f"/range/1/day/{from_date.isoformat()}/{to_date.isoformat()}"
        )
        params = {"adjusted": "true" if adjusted else "false", "sort": "asc", "limit": "50000"}
        self._limiter.acquire()
        response = self._client.get(url, params=params)
        response.raise_for_status()
        return _normalize_aggs(occ, response.json())


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


def _normalize_contracts(payload: object) -> list[OptionContractRef]:
    if not isinstance(payload, dict):
        raise PolygonError(f"unexpected contracts payload shape: {type(payload)!r}")
    results = payload.get("results")
    if results is None:
        return []
    if not isinstance(results, list):
        raise PolygonError(f"unexpected contracts results shape: {type(results)!r}")

    out: list[OptionContractRef] = []
    for entry in results:
        if not isinstance(entry, dict):
            continue
        ticker_raw = entry.get("ticker")
        underlying = entry.get("underlying_ticker")
        contract_type = entry.get("contract_type")
        strike_raw = entry.get("strike_price")
        exp_raw = entry.get("expiration_date")

        if not isinstance(ticker_raw, str) or not isinstance(underlying, str):
            continue
        if contract_type not in ("call", "put"):
            continue
        if not isinstance(strike_raw, (int, float)):
            continue
        if not isinstance(exp_raw, str):
            continue
        try:
            expiration = date.fromisoformat(exp_raw)
        except ValueError:
            log.warning("polygon.bad_contract_expiration", ticker=ticker_raw, raw=exp_raw)
            continue

        occ = ticker_raw[2:] if ticker_raw.startswith("O:") else ticker_raw
        out.append(
            OptionContractRef(
                occ=occ,
                underlying=underlying,
                expiration=expiration,
                strike=float(strike_raw),
                option_type=contract_type,
            )
        )
    return out


def _normalize_aggs(occ: str, payload: object) -> list[OptionDailyAgg]:
    if not isinstance(payload, dict):
        raise PolygonError(f"unexpected aggs payload shape: {type(payload)!r}")
    # Polygon returns ``status: "OK"`` for success and ``"DELAYED"``/``"NOT_AUTHORIZED"`` etc.
    # An empty results list is normal for contracts that never traded — don't raise on it.
    results = payload.get("results")
    if results is None:
        return []
    if not isinstance(results, list):
        raise PolygonError(f"unexpected aggs results shape: {type(results)!r}")

    out: list[OptionDailyAgg] = []
    for bar in results:
        if not isinstance(bar, dict):
            continue
        ts = bar.get("t")
        if not isinstance(ts, (int, float)):
            continue
        # Polygon bar timestamps are UTC ms at the start of the bar window.
        bar_date = datetime.fromtimestamp(ts / 1000.0, UTC).date()
        out.append(
            OptionDailyAgg(
                occ=occ,
                date=bar_date,
                open=_get_float(bar, "o"),
                high=_get_float(bar, "h"),
                low=_get_float(bar, "l"),
                close=_get_float(bar, "c"),
                volume=_get_int(bar, "v"),
            )
        )
    return out
