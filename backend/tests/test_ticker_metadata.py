"""Tests for the ticker metadata fetcher."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from db.models.market import Ticker
from ingestion.finnhub_client import CompanyProfile
from ingestion.ticker_metadata import fetch_metadata


class FakeProfileClient:
    def __init__(self, profiles: dict[str, CompanyProfile | None]) -> None:
        self._profiles = profiles
        self.calls: list[str] = []

    def get_company_profile(self, symbol: str) -> CompanyProfile | None:
        self.calls.append(symbol)
        return self._profiles.get(symbol)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "metadata.db"
    url = f"sqlite:///{db_path}"
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    s.add(Ticker(symbol="AAPL", name="Apple Inc.", is_active=True))
    s.add(Ticker(symbol="MSFT", name=None, is_active=True))
    s.add(Ticker(symbol="QQQ", is_active=True))  # ETF; Finnhub returns empty
    s.add(Ticker(symbol="OLD", is_active=False))
    s.commit()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def test_fetch_metadata_updates_active_tickers(session: Session) -> None:
    client = FakeProfileClient(
        {
            "AAPL": CompanyProfile(
                symbol="AAPL", name="Apple Inc.", sector="Technology", market_cap=3.5e12
            ),
            "MSFT": CompanyProfile(
                symbol="MSFT", name="Microsoft Corp", sector="Software", market_cap=2.5e12
            ),
            "QQQ": None,
        }
    )

    summary = fetch_metadata(session, client)

    assert summary.symbols_processed == 3
    assert summary.rows_updated == 2
    assert summary.rows_skipped == 1
    assert "OLD" not in client.calls

    aapl = session.get(Ticker, "AAPL")
    assert aapl is not None and aapl.sector == "Technology"
    assert aapl.market_cap == pytest.approx(3.5e12)
    msft = session.get(Ticker, "MSFT")
    assert msft is not None and msft.name == "Microsoft Corp"
    assert msft.sector == "Software"
    qqq = session.get(Ticker, "QQQ")
    assert qqq is not None and qqq.sector is None


def test_only_missing_skips_already_populated(session: Session) -> None:
    aapl = session.get(Ticker, "AAPL")
    assert aapl is not None
    aapl.sector = "Tech"
    session.commit()

    client = FakeProfileClient(
        {
            "MSFT": CompanyProfile(symbol="MSFT", name=None, sector="Software", market_cap=2.5e12),
        }
    )

    summary = fetch_metadata(session, client, only_missing=True)

    assert "AAPL" not in client.calls
    assert "MSFT" in client.calls
    assert summary.rows_updated == 1


def test_explicit_symbols_filter_takes_precedence(session: Session) -> None:
    client = FakeProfileClient(
        {
            "AAPL": CompanyProfile(
                symbol="AAPL", name=None, sector="Technology", market_cap=3.5e12
            ),
        }
    )
    summary = fetch_metadata(session, client, symbols=["AAPL"])
    assert client.calls == ["AAPL"]
    assert summary.rows_updated == 1


def test_does_not_overwrite_existing_name(session: Session) -> None:
    client = FakeProfileClient(
        {
            "AAPL": CompanyProfile(
                symbol="AAPL", name="Different Name", sector="Tech", market_cap=1.0e12
            ),
        }
    )
    fetch_metadata(session, client, symbols=["AAPL"])
    aapl = session.get(Ticker, "AAPL")
    assert aapl is not None and aapl.name == "Apple Inc."
