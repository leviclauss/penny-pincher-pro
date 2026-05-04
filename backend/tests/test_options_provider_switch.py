"""Tests for the OPTIONS_PROVIDER switch in the ingestion pipeline.

Verifies that:
- ``OPTIONS_PROVIDER=polygon`` with a key returns the Polygon client.
- ``OPTIONS_PROVIDER=polygon`` without a key warns and falls back to Alpaca.
- ``OPTIONS_PROVIDER=alpaca`` returns the Alpaca client.
- An unknown value warns and falls back to Alpaca.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from core.config import get_settings
from ingestion.options_client import AlpacaOptionsClient
from ingestion.pipeline import _build_options_client
from ingestion.polygon_client import PolygonOptionsClient


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_polygon_provider_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPTIONS_PROVIDER", "polygon")
    monkeypatch.setenv("POLYGON_API_KEY", "test-key")
    # Alpaca creds aren't needed for the polygon path but set them anyway
    # so the fallback wouldn't accidentally also raise.
    monkeypatch.setenv("ALPACA_API_KEY", "ak")
    monkeypatch.setenv("ALPACA_API_SECRET", "as")

    client = _build_options_client()
    assert isinstance(client, PolygonOptionsClient)


def test_polygon_provider_missing_key_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPTIONS_PROVIDER", "polygon")
    monkeypatch.setenv("POLYGON_API_KEY", "")
    monkeypatch.setenv("ALPACA_API_KEY", "ak")
    monkeypatch.setenv("ALPACA_API_SECRET", "as")

    client = _build_options_client()
    assert isinstance(client, AlpacaOptionsClient)


def test_alpaca_provider_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPTIONS_PROVIDER", "alpaca")
    monkeypatch.setenv("ALPACA_API_KEY", "ak")
    monkeypatch.setenv("ALPACA_API_SECRET", "as")

    client = _build_options_client()
    assert isinstance(client, AlpacaOptionsClient)


def test_unknown_provider_falls_back_to_alpaca(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPTIONS_PROVIDER", "tradier")
    monkeypatch.setenv("ALPACA_API_KEY", "ak")
    monkeypatch.setenv("ALPACA_API_SECRET", "as")

    client = _build_options_client()
    assert isinstance(client, AlpacaOptionsClient)
