"""Thin wrapper around ``alpaca-py``'s ``OptionHistoricalDataClient``.

Mirrors the patterns in ``ingestion/alpaca_client.py``: tenacity retry, an
injectable underlying client for tests, structured logging, and a normalized
record type that decouples callers from SDK shape changes.

OCC option symbols (e.g. ``AAPL240517C00170000``) are parsed here so callers
work in domain terms (underlying, expiration, strike, type).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionChainRequest
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.config import get_settings
from core.logging import get_logger

log = get_logger(__name__)

OCC_SYMBOL_RE = re.compile(
    r"^(?P<root>[A-Z]+)(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<cp>[CP])(?P<strike>\d{8})$"
)


@dataclass(frozen=True, slots=True)
class OptionSnapshotRecord:
    """Normalized option-chain row matching the ``options_snapshot`` table."""

    symbol: str
    expiration: date
    strike: float
    option_type: str
    bid: float | None
    ask: float | None
    last: float | None
    volume: int | None
    open_interest: int | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    iv: float | None


class _ChainClient(Protocol):
    def get_option_chain(self, request_params: OptionChainRequest) -> object: ...


_RETRYABLE_EXCEPTIONS = (ConnectionError, TimeoutError, OSError)


class AlpacaOptionsError(RuntimeError):
    """Raised when Alpaca options data fetch fails non-retryably."""


class OCCParseError(ValueError):
    """Raised when an OCC option symbol can't be parsed."""


class AlpacaOptionsClient:
    """Sync wrapper around the Alpaca options historical data SDK."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        feed: str | None = None,
        *,
        client: _ChainClient | None = None,
    ) -> None:
        settings = get_settings()
        self._feed = feed or settings.alpaca_options_feed
        if client is not None:
            self._client: _ChainClient = client
        else:
            key = api_key if api_key is not None else settings.alpaca_api_key
            secret = api_secret if api_secret is not None else settings.alpaca_api_secret
            if not key or not secret:
                raise AlpacaOptionsError(
                    "Alpaca credentials missing — set ALPACA_API_KEY / ALPACA_API_SECRET"
                )
            self._client = OptionHistoricalDataClient(key, secret)

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
        """Fetch the option chain for ``underlying`` filtered server-side."""
        log.info(
            "alpaca.get_option_chain",
            underlying=underlying,
            expiration_gte=str(expiration_gte) if expiration_gte else None,
            expiration_lte=str(expiration_lte) if expiration_lte else None,
            strike_gte=strike_gte,
            strike_lte=strike_lte,
            feed=self._feed,
        )
        request = OptionChainRequest(
            underlying_symbol=underlying,
            feed=self._feed,
            expiration_date_gte=expiration_gte,
            expiration_date_lte=expiration_lte,
            strike_price_gte=strike_gte,
            strike_price_lte=strike_lte,
        )
        response = self._client.get_option_chain(request)
        return _normalize_chain(response)


def parse_occ_symbol(occ: str) -> tuple[str, date, str, float]:
    """Parse OCC-format option symbol → (root, expiration, type, strike)."""
    match = OCC_SYMBOL_RE.match(occ)
    if not match:
        raise OCCParseError(f"not a valid OCC option symbol: {occ!r}")
    root = match["root"]
    expiration = date(2000 + int(match["yy"]), int(match["mm"]), int(match["dd"]))
    option_type = "call" if match["cp"] == "C" else "put"
    strike = int(match["strike"]) / 1000.0
    return root, expiration, option_type, strike


def _normalize_chain(response: object) -> list[OptionSnapshotRecord]:
    if not isinstance(response, dict):
        raise AlpacaOptionsError(f"unexpected chain response shape: {type(response)!r}")

    records: list[OptionSnapshotRecord] = []
    for occ_symbol, snapshot in response.items():
        try:
            root, expiration, option_type, strike = parse_occ_symbol(occ_symbol)
        except OCCParseError:
            log.warning("options.unparsable_symbol", symbol=occ_symbol)
            continue

        quote = getattr(snapshot, "latest_quote", None)
        trade = getattr(snapshot, "latest_trade", None)
        greeks = getattr(snapshot, "greeks", None)
        iv = getattr(snapshot, "implied_volatility", None)

        records.append(
            OptionSnapshotRecord(
                symbol=root,
                expiration=expiration,
                strike=strike,
                option_type=option_type,
                bid=_get(quote, "bid_price"),
                ask=_get(quote, "ask_price"),
                last=_get(trade, "price"),
                volume=None,
                open_interest=None,
                delta=_get(greeks, "delta"),
                gamma=_get(greeks, "gamma"),
                theta=_get(greeks, "theta"),
                vega=_get(greeks, "vega"),
                iv=float(iv) if iv is not None else None,
            )
        )
    return records


def _get(obj: object, attr: str) -> float | None:
    if obj is None:
        return None
    value = getattr(obj, attr, None)
    return None if value is None else float(value)
