"""End-to-end pipeline tests.

Exercises run_full and run_incremental over a fresh migrated SQLite + a fake
Alpaca client, asserting that:
- bars and indicators land in the DB
- incremental run only writes new indicator rows
- the CLI parses --full / --incremental flags correctly (smoke)
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from db.models.market import BarDaily, Earnings, IndicatorDaily, MacroDaily, Ticker
from ingestion.finnhub_client import EarningsRecord
from ingestion.macro import VIX9D_SYMBOL, VIX_SYMBOL
from ingestion.options_client import OptionSnapshotRecord
from ingestion.pipeline import run_full, run_incremental
from ingestion.yahoo_client import IndexBarRecord
from tests.test_bars_fetcher import FIXTURE_END, FakeAlpacaClient
from tests.test_bars_fetcher import _fake_client_for as build_fake
from tests.test_earnings_fetcher import FakeFinnhubClient
from tests.test_macro_fetcher import FakeYahooClient
from tests.test_options_fetcher import FakeOptionsClient


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    db_path = tmp_path / "pipeline.db"
    url = f"sqlite:///{db_path}"
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    s.add(Ticker(symbol="AAA", is_active=True))
    s.commit()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def test_run_full_writes_bars_and_indicators(session: Session) -> None:
    client: FakeAlpacaClient = build_fake(["AAA"])
    summary = run_full(session, client, ["AAA"], years=2, end=FIXTURE_END)  # type: ignore[arg-type]

    assert summary.mode == "full"
    assert summary.fetch.bars_written > 0
    assert summary.indicators_written > 0
    assert summary.symbols_processed == 1

    bar_count = session.execute(
        select(func.count()).select_from(BarDaily).where(BarDaily.symbol == "AAA")
    ).scalar_one()
    ind_count = session.execute(
        select(func.count()).select_from(IndicatorDaily).where(IndicatorDaily.symbol == "AAA")
    ).scalar_one()
    assert ind_count == bar_count


def test_run_incremental_only_writes_new_indicator_rows(session: Session) -> None:
    client: FakeAlpacaClient = build_fake(["AAA"])
    run_full(session, client, ["AAA"], years=2, end=date(2024, 6, 30))  # type: ignore[arg-type]

    initial_ind_count = session.execute(
        select(func.count()).select_from(IndicatorDaily).where(IndicatorDaily.symbol == "AAA")
    ).scalar_one()
    assert initial_ind_count > 0

    summary = run_incremental(session, client, ["AAA"], end=FIXTURE_END)  # type: ignore[arg-type]

    final_ind_count = session.execute(
        select(func.count()).select_from(IndicatorDaily).where(IndicatorDaily.symbol == "AAA")
    ).scalar_one()
    delta = final_ind_count - initial_ind_count
    assert summary.indicators_written == delta
    assert delta > 0


def test_cli_help_lists_modes() -> None:
    from click.testing import CliRunner

    from ingestion.pipeline import cli

    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "--full" in result.output
    assert "--incremental" in result.output
    assert "--skip-options" in result.output


def _aapl_chain_for(expiration: date, spot: float) -> list[OptionSnapshotRecord]:
    return [
        OptionSnapshotRecord(
            symbol="AAA",
            expiration=expiration,
            strike=spot,
            option_type=kind,
            bid=1.0,
            ask=1.05,
            last=None,
            volume=None,
            open_interest=None,
            delta=None,
            gamma=None,
            theta=None,
            vega=None,
            iv=0.30,
        )
        for kind in ("call", "put")
    ]


def test_run_full_with_options_writes_iv_atm(session: Session) -> None:
    client: FakeAlpacaClient = build_fake(["AAA"])
    run_full(session, client, ["AAA"], years=2, end=FIXTURE_END)  # type: ignore[arg-type]

    latest_bar_date, latest_close = session.execute(
        select(BarDaily.date, BarDaily.close)
        .where(BarDaily.symbol == "AAA")
        .order_by(BarDaily.date.desc())
        .limit(1)
    ).one()

    expiration = (
        FIXTURE_END.replace(month=FIXTURE_END.month + 1)
        if FIXTURE_END.month < 12
        else date(FIXTURE_END.year + 1, 1, FIXTURE_END.day)
    )
    options_client = FakeOptionsClient({"AAA": _aapl_chain_for(expiration, float(latest_close))})

    summary = run_full(
        session,
        client,  # type: ignore[arg-type]
        ["AAA"],
        years=2,
        end=FIXTURE_END,
        options_client=options_client,
    )

    assert summary.options is not None
    assert summary.options.contracts_written == 2
    assert summary.iv.iv_rows_written == 1

    # IV is stored on the symbol's latest bar date so the API (which joins
    # IndicatorDaily to that date) sees it on calendar days the pipeline ran
    # without a fresh bar.
    iv_atm = session.execute(
        select(IndicatorDaily.iv_atm)
        .where(IndicatorDaily.symbol == "AAA")
        .where(IndicatorDaily.date == latest_bar_date)
    ).scalar_one()
    assert iv_atm == pytest.approx(0.30, abs=1e-6)


def test_skip_options_leaves_iv_null(session: Session) -> None:
    client: FakeAlpacaClient = build_fake(["AAA"])
    summary = run_full(
        session,
        client,  # type: ignore[arg-type]
        ["AAA"],
        years=2,
        end=FIXTURE_END,
        skip_options=True,
    )

    assert summary.options is None
    assert summary.iv.iv_rows_written == 0

    iv_count_with_value = session.execute(
        select(func.count())
        .select_from(IndicatorDaily)
        .where(IndicatorDaily.symbol == "AAA")
        .where(IndicatorDaily.iv_atm.isnot(None))
    ).scalar_one()
    assert iv_count_with_value == 0


def test_cli_advertises_skip_flags() -> None:
    from click.testing import CliRunner

    from ingestion.pipeline import cli

    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "--skip-options" in result.output
    assert "--skip-earnings" in result.output
    assert "--skip-macro" in result.output


def test_run_full_with_earnings_and_macro(session: Session) -> None:
    client: FakeAlpacaClient = build_fake(["AAA"])
    finnhub = FakeFinnhubClient(
        [
            EarningsRecord(symbol="AAA", earnings_date=date(2026, 5, 5), time_of_day="AMC"),
            EarningsRecord(symbol="OFFLIST", earnings_date=date(2026, 5, 6), time_of_day=None),
        ]
    )
    yahoo = FakeYahooClient(
        {
            VIX_SYMBOL: [
                IndexBarRecord(symbol=VIX_SYMBOL, date=FIXTURE_END, close=18.0),
            ],
            VIX9D_SYMBOL: [
                IndexBarRecord(symbol=VIX9D_SYMBOL, date=FIXTURE_END, close=16.0),
            ],
        }
    )

    summary = run_full(
        session,
        client,  # type: ignore[arg-type]
        ["AAA"],
        years=2,
        end=FIXTURE_END,
        skip_options=True,
        earnings_client=finnhub,
        macro_client=yahoo,
    )

    assert summary.earnings is not None
    assert summary.earnings.rows_written == 1
    assert summary.macro is not None
    assert summary.macro.rows_written >= 1

    earnings_symbols = session.execute(select(Earnings.symbol)).scalars().all()
    assert earnings_symbols == ["AAA"]

    vix_close = session.execute(
        select(MacroDaily.vix_close).where(MacroDaily.date == FIXTURE_END)
    ).scalar_one()
    assert vix_close == pytest.approx(18.0)


def test_skip_earnings_and_macro_keeps_tables_empty(session: Session) -> None:
    client: FakeAlpacaClient = build_fake(["AAA"])
    summary = run_full(
        session,
        client,  # type: ignore[arg-type]
        ["AAA"],
        years=2,
        end=FIXTURE_END,
        skip_options=True,
        skip_earnings=True,
        skip_macro=True,
    )

    assert summary.earnings is None
    assert summary.macro is None
    assert session.execute(select(func.count()).select_from(Earnings)).scalar_one() == 0
    assert session.execute(select(func.count()).select_from(MacroDaily)).scalar_one() == 0
