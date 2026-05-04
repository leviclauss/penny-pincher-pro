"""Tests for the Polygon S3 flat-file client.

Uses a fake S3 client that returns in-memory gzipped CSV bytes. Verifies:
- correct CSV parsing + OCC extraction
- symbol filtering
- max_dte filtering
- NoSuchKey → empty iterator
- AccessDenied → FlatFileError
- malformed rows are skipped
"""

from __future__ import annotations

import csv
import gzip
import io
from datetime import date
from typing import Any

import pytest

from ingestion.polygon_flatfiles import (
    EXPECTED_COLUMNS,
    FlatFileError,
    PolygonFlatFileClient,
)

TS_20240513 = "1715558400000"
DAY_KEY = "us_options_opra/day_aggs_v1/2024/05/2024-05-13.csv.gz"


def _row(
    occ: str,
    o: str = "2.10",
    h: str = "2.20",
    lo: str = "2.00",
    c: str = "2.15",
    vol: str = "100",
    vwap: str = "2.12",
    ts: str = TS_20240513,
    txns: str = "50",
) -> list[str]:
    return [occ, o, h, lo, c, vol, vwap, ts, txns]


def _make_csv_gz(
    rows: list[list[str]],
    header: list[str] | None = None,
) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    if header is None:
        header = list(EXPECTED_COLUMNS)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return gzip.compress(buf.getvalue().encode("utf-8"))


class FakeS3Client:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        key = kwargs.get("Key", "")
        if key in self._objects:
            return {"Body": io.BytesIO(self._objects[key])}
        error = type("ClientError", (Exception,), {})
        exc = error(f"NoSuchKey: {key}")
        exc.response = {"Error": {"Code": "NoSuchKey"}}  # type: ignore[attr-defined]
        raise exc


class AccessDeniedS3Client:
    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        error = type("ClientError", (Exception,), {})
        exc = error("AccessDenied")
        exc.response = {"Error": {"Code": "AccessDenied"}}  # type: ignore[attr-defined]
        raise exc


def test_parses_rows_and_filters_by_symbol() -> None:
    rows = [
        _row("O:AAPL240517C00170000"),
        _row("O:MSFT240517P00400000", c="3.05", vol="200"),
    ]
    data = _make_csv_gz(rows)
    s3 = FakeS3Client({DAY_KEY: data})
    client = PolygonFlatFileClient(s3_client=s3)

    results = list(client.iter_day(date(2024, 5, 13), frozenset({"AAPL"})))
    assert len(results) == 1
    agg = results[0]
    assert agg.underlying == "AAPL"
    assert agg.strike == 170.0
    assert agg.option_type == "call"
    assert agg.expiration == date(2024, 5, 17)
    assert agg.as_of == date(2024, 5, 13)
    assert agg.close == 2.15
    assert agg.volume == 100


def test_filters_by_max_dte() -> None:
    rows = [
        _row("O:AAPL240517C00170000"),
        _row("O:AAPL250117C00170000", c="0.52", vol="10"),
    ]
    data = _make_csv_gz(rows)
    s3 = FakeS3Client({DAY_KEY: data})
    client = PolygonFlatFileClient(s3_client=s3)

    results = list(
        client.iter_day(
            date(2024, 5, 13),
            frozenset({"AAPL"}),
            max_dte=60,
        )
    )
    assert len(results) == 1
    assert results[0].expiration == date(2024, 5, 17)


def test_no_such_key_yields_nothing() -> None:
    client = PolygonFlatFileClient(s3_client=FakeS3Client({}))
    results = list(client.iter_day(date(2024, 5, 13), frozenset({"AAPL"})))
    assert results == []


def test_access_denied_raises_flat_file_error() -> None:
    client = PolygonFlatFileClient(s3_client=AccessDeniedS3Client())
    with pytest.raises(FlatFileError, match="Access denied"):
        list(client.iter_day(date(2024, 5, 13), frozenset({"AAPL"})))


def test_malformed_rows_are_skipped() -> None:
    rows = [
        _row("O:AAPL240517C00170000"),
        _row("O:INVALID"),
        ["O:AAPL240517C00170000", "2.10"],  # too few columns
    ]
    data = _make_csv_gz(rows)
    s3 = FakeS3Client({DAY_KEY: data})
    client = PolygonFlatFileClient(s3_client=s3)

    results = list(client.iter_day(date(2024, 5, 13), frozenset({"AAPL"})))
    assert len(results) == 1
    assert results[0].occ == "AAPL240517C00170000"


def test_missing_columns_raises_flat_file_error() -> None:
    bad_header = ["ticker", "open", "high"]
    buf = io.StringIO()
    csv.writer(buf).writerow(bad_header)
    data = gzip.compress(buf.getvalue().encode("utf-8"))
    s3 = FakeS3Client({DAY_KEY: data})
    client = PolygonFlatFileClient(s3_client=s3)

    with pytest.raises(FlatFileError, match="missing expected columns"):
        list(client.iter_day(date(2024, 5, 13), frozenset({"AAPL"})))


def test_multiple_symbols_returned() -> None:
    rows = [
        _row("O:AAPL240517C00170000"),
        _row("O:MSFT240517P00400000", c="3.05"),
        _row("O:GOOG240517C03000000", c="5.05"),
    ]
    data = _make_csv_gz(rows)
    s3 = FakeS3Client({DAY_KEY: data})
    client = PolygonFlatFileClient(s3_client=s3)

    results = list(
        client.iter_day(
            date(2024, 5, 13),
            frozenset({"AAPL", "MSFT"}),
        )
    )
    assert len(results) == 2
    underlyings = {r.underlying for r in results}
    assert underlyings == {"AAPL", "MSFT"}


def test_put_option_type_parsed() -> None:
    rows = [_row("O:AAPL240517P00170000")]
    data = _make_csv_gz(rows)
    s3 = FakeS3Client({DAY_KEY: data})
    client = PolygonFlatFileClient(s3_client=s3)

    results = list(client.iter_day(date(2024, 5, 13), frozenset({"AAPL"})))
    assert len(results) == 1
    assert results[0].option_type == "put"
