"""Polygon S3 flat-file client for bulk historical option aggregates.

Polygon publishes daily OHLCV for every US OPRA option contract at
``s3://flatfiles/us_options_opra/day_aggs_v1/{YYYY}/{MM}/{YYYY-MM-DD}.csv.gz``.
One file per trading day, one row per contract that traded.

Compared to the per-contract REST path (``polygon_client.get_contract_aggs``),
flat files replace thousands of API calls with a single S3 GET per day —
cutting a multi-hour backfill to minutes.

Auth uses dedicated S3 credentials issued in the Polygon dashboard (separate
from the REST API key). ``boto3`` is imported lazily so the module loads
without the ``backup-s3`` extra installed.
"""

from __future__ import annotations

import csv
import gzip
import io
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from core.config import get_settings
from core.logging import get_logger
from ingestion.options_client import OCCParseError, parse_occ_symbol

log = get_logger(__name__)

BUCKET = "flatfiles"
KEY_PREFIX = "us_options_opra/day_aggs_v1"

EXPECTED_COLUMNS = (
    "ticker",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "vwap",
    "timestamp",
    "transactions",
)


class FlatFileError(RuntimeError):
    """Raised on S3 access or parsing failures."""


class S3Client(Protocol):
    """Minimal boto3 S3 client shape for test injection."""

    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class FlatFileAgg:
    """One parsed row from a Polygon flat file."""

    occ: str
    underlying: str
    expiration: date
    strike: float
    option_type: str  # "call" or "put"
    as_of: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None


class PolygonFlatFileClient:
    """Stream and parse Polygon's daily option flat files from S3."""

    _s3: S3Client

    def __init__(self, *, s3_client: S3Client | None = None) -> None:
        if s3_client is not None:
            self._s3 = s3_client
            return

        settings = get_settings()
        if not settings.polygon_flatfiles_access_key_id:
            raise FlatFileError(
                "Polygon flat-file credentials missing — set "
                "POLYGON_FLATFILES_ACCESS_KEY_ID and POLYGON_FLATFILES_SECRET_ACCESS_KEY"
            )
        try:
            import boto3  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "flat-file backfill requires the 'backup-s3' extra; "
                "install with `pip install -e .[backup-s3]`"
            ) from exc

        self._s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.polygon_flatfiles_access_key_id,
            aws_secret_access_key=settings.polygon_flatfiles_secret_access_key,
            region_name=settings.polygon_flatfiles_s3_region,
        )

    def iter_day(
        self,
        day: date,
        symbols: frozenset[str],
        *,
        max_dte: int = 60,
    ) -> Iterator[FlatFileAgg]:
        """Stream one day's flat file, yielding rows that match ``symbols``.

        Rows whose underlying is not in ``symbols`` or whose expiration is
        more than ``max_dte`` days past ``day`` are skipped during parsing
        so memory stays proportional to the watchlist, not the full OPRA
        universe.

        Yields nothing (instead of raising) when the file doesn't exist
        (weekends, holidays).
        """
        key = f"{KEY_PREFIX}/{day.year}/{day.month:02d}/{day.isoformat()}.csv.gz"
        try:
            response = self._s3.get_object(Bucket=BUCKET, Key=key)
        except Exception as exc:
            err_code = _client_error_code(exc)
            if err_code == "NoSuchKey":
                log.info("flatfiles.no_file", day=str(day))
                return
            if err_code == "AccessDenied":
                raise FlatFileError(
                    f"Access denied reading s3://{BUCKET}/{key} — "
                    "check POLYGON_FLATFILES_ACCESS_KEY_ID / SECRET"
                ) from exc
            raise FlatFileError(f"S3 error fetching {key}: {exc}") from exc

        body = response["Body"].read()
        text = gzip.decompress(body).decode("utf-8")
        reader = csv.reader(io.StringIO(text))

        header = next(reader, None)
        if header is None:
            return
        col_index = _build_column_index(header)

        exp_cutoff = day.toordinal() + max_dte
        for row in reader:
            record = _parse_row(row, col_index, day, symbols, exp_cutoff)
            if record is not None:
                yield record


def _client_error_code(exc: BaseException) -> str | None:
    """Extract the error code from a botocore ClientError (if it is one)."""
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        err = resp.get("Error")
        if isinstance(err, dict):
            code = err.get("Code")
            if isinstance(code, str):
                return code
    return None


def _build_column_index(header: list[str]) -> dict[str, int]:
    normed = [c.strip().lower() for c in header]
    idx: dict[str, int] = {}
    for col in EXPECTED_COLUMNS:
        if col in normed:
            idx[col] = normed.index(col)
    missing = set(EXPECTED_COLUMNS) - set(idx)
    if missing:
        raise FlatFileError(f"flat file missing expected columns: {sorted(missing)}")
    return idx


def _parse_row(
    row: list[str],
    idx: dict[str, int],
    day: date,
    symbols: frozenset[str],
    exp_cutoff_ordinal: int,
) -> FlatFileAgg | None:
    if len(row) <= max(idx.values()):
        return None

    ticker_raw = row[idx["ticker"]].strip()
    occ = ticker_raw[2:] if ticker_raw.startswith("O:") else ticker_raw
    try:
        underlying, expiration, option_type, strike = parse_occ_symbol(occ)
    except OCCParseError:
        return None

    if underlying not in symbols:
        return None
    if expiration.toordinal() > exp_cutoff_ordinal:
        return None

    return FlatFileAgg(
        occ=occ,
        underlying=underlying,
        expiration=expiration,
        strike=strike,
        option_type=option_type,
        as_of=day,
        open=_safe_float(row[idx["open"]]),
        high=_safe_float(row[idx["high"]]),
        low=_safe_float(row[idx["low"]]),
        close=_safe_float(row[idx["close"]]),
        volume=_safe_int(row[idx["volume"]]),
    )


def _safe_float(val: str) -> float | None:
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val: str) -> int | None:
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None
