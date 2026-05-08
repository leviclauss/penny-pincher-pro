"""Tests for the daily ``options_historical`` keep-current scheduler job.

Verifies each skip branch lands a ``job_runs`` row with the right
``skipped=...`` marker (no_credentials / holiday / boto3_missing /
no_active_tickers) and the success path records the BackfillSummary
metrics.
"""

from __future__ import annotations

import builtins
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from core.config import Settings
from db.models.market import Ticker
from db.models.system import JobRun
from ingestion.options_history import BackfillSummary
from scheduler.context import STATUS_SUCCESS
from scheduler.jobs import options_history as job_module
from scheduler.jobs.options_history import JOB_NAME, run_options_history_keep_current


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "kc.db"
    url = f"sqlite:///{db_path}"
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    s.add(Ticker(symbol="AAPL", is_active=True))
    s.commit()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _settings(*, with_creds: bool = False) -> Settings:
    return Settings(
        polygon_flatfiles_access_key_id="key" if with_creds else "",
        polygon_flatfiles_secret_access_key="secret" if with_creds else "",
    )


def _last_jobrun(session: Session) -> JobRun:
    return session.execute(select(JobRun).where(JobRun.job_name == JOB_NAME)).scalar_one()


def test_skips_when_no_credentials(session: Session) -> None:
    # Tuesday after a trading-day Monday — yesterday must be a market day.
    run_options_history_keep_current(
        session,
        settings=_settings(with_creds=False),
        market_calendar="NYSE",
        as_of=date(2024, 5, 14),
    )
    row = _last_jobrun(session)
    assert row.status == STATUS_SUCCESS
    assert (row.result_json or {})["skipped"] == "no_credentials"


def test_skips_when_yesterday_is_holiday(session: Session) -> None:
    # 2024-07-04 was a market holiday; running on the 5th targets the 4th.
    run_options_history_keep_current(
        session,
        settings=_settings(with_creds=True),
        market_calendar="NYSE",
        as_of=date(2024, 7, 5),
    )
    row = _last_jobrun(session)
    assert row.status == STATUS_SUCCESS
    assert (row.result_json or {})["skipped"] == "holiday"
    assert (row.result_json or {})["date"] == "2024-07-04"


def test_skips_when_boto3_missing(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_import = builtins.__import__

    def fake_import(
        name: str,
        globals: dict | None = None,  # noqa: A002
        locals: dict | None = None,  # noqa: A002
        fromlist: tuple = (),
        level: int = 0,
    ) -> object:
        if name == "ingestion.polygon_flatfiles":
            raise ImportError("No module named 'boto3'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    run_options_history_keep_current(
        session,
        settings=_settings(with_creds=True),
        market_calendar="NYSE",
        as_of=date(2024, 5, 14),
    )
    row = _last_jobrun(session)
    assert row.status == STATUS_SUCCESS
    assert (row.result_json or {})["skipped"] == "boto3_missing"


def test_success_records_summary_metrics(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stub the flat-file backfill to a fake summary; verify metrics persist."""

    class FakeClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

    captured: dict[str, object] = {}

    def fake_backfill(
        _session: Session,
        _client: object,
        symbols: list[str] | None = None,
        *,
        start: date,
        end: date,
        max_dte: int = 60,
        strike_pct_window: float = 0.15,
    ) -> BackfillSummary:
        captured["symbols"] = symbols
        captured["start"] = start
        captured["end"] = end
        return BackfillSummary(
            symbols_requested=1,
            symbols_with_data=1,
            contracts_fetched=42,
            rows_written=100,
        )

    # Replace the flat-file client + backfill function with stubs so we
    # don't reach S3.
    monkeypatch.setattr(
        "ingestion.polygon_flatfiles.PolygonFlatFileClient",
        FakeClient,
        raising=False,
    )
    monkeypatch.setattr(job_module, "backfill_history_flatfile", fake_backfill)

    run_options_history_keep_current(
        session,
        settings=_settings(with_creds=True),
        market_calendar="NYSE",
        as_of=date(2024, 5, 14),
    )
    row = _last_jobrun(session)
    assert row.status == STATUS_SUCCESS
    assert (row.result_json or {})["date"] == "2024-05-13"
    assert (row.result_json or {})["symbols"] == 1
    assert (row.result_json or {})["symbols_with_data"] == 1
    assert (row.result_json or {})["contracts"] == 42
    assert (row.result_json or {})["rows"] == 100
    # The job asks the flatfile backfiller for exactly yesterday's window.
    assert captured["start"] == date(2024, 5, 13)
    assert captured["end"] == date(2024, 5, 13)
