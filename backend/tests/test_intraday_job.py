"""End-to-end tests for the intraday alert pulse job.

Exercises the four execution branches and the dedup-with-morning-digest
suppression rule. Uses an alembic-migrated SQLite DB and fake quote/chain
sources so no network or scheduler is involved.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import select

from alembic import command
from alerts.channels.base import Channel, ChannelResult
from db import get_session
from db.models.alerts import Alert
from db.models.market import BarDaily, IndicatorDaily, Ticker
from db.models.screener import FilterConfig
from db.models.system import JobRun
from ingestion.alpaca_client import QuoteRecord
from ingestion.options_client import OptionSnapshotRecord
from scheduler.jobs import intraday as intraday_job
from scheduler.jobs.intraday import JOB_NAME, run_intraday_pulse

AS_OF = date(2026, 5, 4)  # Monday
RTH_NOW = datetime(2026, 5, 4, 17, 0, tzinfo=UTC)  # 13:00 ET, mid-session
PRE_OPEN_NOW = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)  # 08:00 ET, before open


class _FakeChannel:
    id = "telegram"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def send(self, alert_type: str, payload: dict[str, Any]) -> ChannelResult:
        self.calls.append((alert_type, payload))
        return ChannelResult(True, "msg-1", None)


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_path = tmp_path / "intraday_job.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    monkeypatch.setenv("INTRADAY_IV_SPIKE_ENABLED", "true")
    monkeypatch.setenv("INTRADAY_IV_SPIKE_PCT", "0.20")
    monkeypatch.setenv("INTRADAY_IV_SPIKE_INTERVAL_MINUTES", "30")
    monkeypatch.setenv("INTRADAY_QUOTE_MAX_AGE_S", "90")

    from core.config import get_settings
    from db import session as db_session

    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()

    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    intraday_job.reset_iv_throttle()

    yield

    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()
    intraday_job.reset_iv_throttle()


def _patch_channel(monkeypatch: pytest.MonkeyPatch) -> _FakeChannel:
    fake = _FakeChannel()
    registry: dict[str, Channel] = {"telegram": fake}
    monkeypatch.setattr("alerts.dispatcher.CHANNELS", registry)
    return fake


def _seed_watchlist(symbol: str = "AAPL") -> None:
    with get_session() as session:
        session.add(
            Ticker(
                symbol=symbol,
                is_active=True,
                is_hidden=False,
                added_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )


def _seed_bars_and_indicators(
    symbol: str = "AAPL",
    *,
    days: int = 60,
    base_close: float = 175.0,
    last_date: date = AS_OF - timedelta(days=1),
    iv_atm: float | None = 0.25,
) -> None:
    """Seed enough daily history for RSI(14) to compute + a baseline IV row."""
    with get_session() as session:
        for i in range(days):
            d = last_date - timedelta(days=days - 1 - i)
            close = base_close + (i % 5) * 0.5  # mild oscillation, deterministic
            session.add(
                BarDaily(
                    symbol=symbol,
                    date=d,
                    open=close - 0.1,
                    high=close + 0.5,
                    low=close - 0.5,
                    close=close,
                    volume=1_000_000,
                )
            )
        session.add(
            IndicatorDaily(
                symbol=symbol,
                date=last_date,
                ema_20=base_close,
                ema_50=base_close - 1.0,
                ema_200=base_close - 5.0,
                rsi_14=45.0,
                iv_atm=iv_atm,
            )
        )


def _seed_active_screener_config() -> None:
    """A config that's trivially passable (every active ticker passes)."""
    with get_session() as session:
        session.add(
            FilterConfig(
                name="Always Pass",
                description="test config",
                is_active=True,
                config_json={
                    "filters": [
                        {
                            "id": "min_market_cap",
                            "required": False,
                            "params": {"min_market_cap": 0},
                        }
                    ],
                    "scoring": {"weights": {}},
                },
            )
        )


class _QuoteSourceState:
    """Mutable timestamp anchor so a quote stays "fresh" relative to the
    test's chosen ``now`` even across multiple ticks."""

    def __init__(self, *, anchor: datetime, age_seconds: int, mid: float, symbol: str) -> None:
        self.anchor = anchor
        self.age_seconds = age_seconds
        self.mid = mid
        self.symbol = symbol
        self.received: list[list[str]] = []

    def __call__(self, symbols: list[str]) -> dict[str, QuoteRecord]:
        self.received.append(list(symbols))
        timestamp = self.anchor - timedelta(seconds=self.age_seconds)
        return {
            self.symbol: QuoteRecord(
                symbol=self.symbol,
                timestamp=timestamp,
                bid=self.mid - 0.05,
                ask=self.mid + 0.05,
            )
        }


def _quote_source_factory(
    symbol: str = "AAPL", *, age_seconds: int = 5, mid: float = 175.0
) -> tuple[_QuoteSourceState, list[list[str]]]:
    state = _QuoteSourceState(anchor=RTH_NOW, age_seconds=age_seconds, mid=mid, symbol=symbol)
    return state, state.received


def _latest_jobrun() -> JobRun:
    with get_session() as session:
        row = (
            session.execute(
                select(JobRun).where(JobRun.job_name == JOB_NAME).order_by(JobRun.id.desc())
            )
            .scalars()
            .first()
        )
    assert row is not None
    return row


def test_holiday_skip_writes_jobrun(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_channel(monkeypatch)
    monkeypatch.setattr(intraday_job, "_market_schedule", lambda *_args, **_kw: None)
    quote_source, _ = _quote_source_factory()

    with get_session() as session:
        run_intraday_pulse(
            session,
            quote_source=quote_source,
            market_calendar="NYSE",
            as_of=AS_OF,
            now=RTH_NOW,
        )

    job = _latest_jobrun()
    assert (job.result_json or {}).get("skipped") == "holiday"


def test_outside_rth_skip_writes_jobrun(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_channel(monkeypatch)
    quote_source, received = _quote_source_factory()

    with get_session() as session:
        run_intraday_pulse(
            session,
            quote_source=quote_source,
            market_calendar="NYSE",
            as_of=AS_OF,
            now=PRE_OPEN_NOW,
        )

    job = _latest_jobrun()
    assert (job.result_json or {}).get("skipped") == "outside_rth"
    # Quotes never fetched once we know we're outside RTH.
    assert received == []


def test_stale_quotes_skip(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_channel(monkeypatch)
    _seed_watchlist()
    quote_source, _ = _quote_source_factory(age_seconds=300)  # > 90s threshold

    with get_session() as session:
        run_intraday_pulse(
            session,
            quote_source=quote_source,
            as_of=AS_OF,
            now=RTH_NOW,
        )

    job = _latest_jobrun()
    assert (job.result_json or {}).get("skipped") == "stale_quotes"
    assert fake.calls == []


def test_setup_dispatch_and_per_day_dedup(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_channel(monkeypatch)
    _seed_watchlist()
    _seed_bars_and_indicators()
    _seed_active_screener_config()
    quote_source, _ = _quote_source_factory()

    # First tick fires.
    with get_session() as session:
        run_intraday_pulse(session, quote_source=quote_source, as_of=AS_OF, now=RTH_NOW)
    assert len(fake.calls) == 1
    alert_type, payload = fake.calls[0]
    assert alert_type == "setup_triggered"
    assert payload["symbol"] == "AAPL"
    assert payload["as_of"] == AS_OF.isoformat()

    job = _latest_jobrun()
    result = job.result_json or {}
    assert result["setup_fired"] == 1
    assert result["setup_suppressed_morning"] == 0
    assert result["setup_suppressed_dedup"] == 0

    # Second tick same day → suppressed via per-(symbol, day) dedup.
    with get_session() as session:
        run_intraday_pulse(session, quote_source=quote_source, as_of=AS_OF, now=RTH_NOW)
    assert len(fake.calls) == 1  # no new dispatch
    job2 = _latest_jobrun()
    result2 = job2.result_json or {}
    assert result2["setup_fired"] == 0
    assert result2["setup_suppressed_dedup"] == 1


def test_setup_suppressed_when_in_morning_digest(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_channel(monkeypatch)
    _seed_watchlist()
    _seed_bars_and_indicators()
    _seed_active_screener_config()

    # Pre-seed today's morning digest with AAPL in screener_hits.
    with get_session() as session:
        session.add(
            Alert(
                alert_type="morning_digest",
                payload_json={
                    "as_of": AS_OF.isoformat(),
                    "screener_hits": [{"symbol": "AAPL"}],
                },
            )
        )

    quote_source, _ = _quote_source_factory()
    with get_session() as session:
        run_intraday_pulse(session, quote_source=quote_source, as_of=AS_OF, now=RTH_NOW)

    assert fake.calls == []
    job = _latest_jobrun()
    result = job.result_json or {}
    assert result["setup_fired"] == 0
    assert result["setup_suppressed_morning"] == 1


def test_iv_spike_fires_when_threshold_crossed(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_channel(monkeypatch)
    _seed_watchlist()
    # Baseline IV in DB = 0.20; intraday chain returns ATM IV ≈ 0.30 → +50%.
    _seed_bars_and_indicators(iv_atm=0.20)
    quote_source, _ = _quote_source_factory(mid=175.0)

    def chain_source(_symbol: str) -> list[OptionSnapshotRecord]:
        return [
            OptionSnapshotRecord(
                symbol="AAPL",
                expiration=AS_OF + timedelta(days=30),
                strike=175.0,
                option_type="call",
                bid=None,
                ask=None,
                last=None,
                volume=None,
                open_interest=None,
                delta=None,
                gamma=None,
                theta=None,
                vega=None,
                iv=0.30,
            ),
            OptionSnapshotRecord(
                symbol="AAPL",
                expiration=AS_OF + timedelta(days=30),
                strike=175.0,
                option_type="put",
                bid=None,
                ask=None,
                last=None,
                volume=None,
                open_interest=None,
                delta=None,
                gamma=None,
                theta=None,
                vega=None,
                iv=0.30,
            ),
        ]

    with get_session() as session:
        run_intraday_pulse(
            session,
            quote_source=quote_source,
            chain_source=chain_source,
            as_of=AS_OF,
            now=RTH_NOW,
        )

    iv_calls = [c for c in fake.calls if c[0] == "iv_spike"]
    assert len(iv_calls) == 1
    payload = iv_calls[0][1]
    assert payload["symbol"] == "AAPL"
    assert payload["baseline_iv"] == pytest.approx(0.20)
    assert payload["current_iv"] == pytest.approx(0.30)
    assert payload["pct_change"] == pytest.approx(0.5)

    job = _latest_jobrun()
    result = job.result_json or {}
    assert result["iv_spike_fired"] == 1
    assert result["iv_spike_checked"] == 1


def test_iv_spike_below_threshold_does_not_fire(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_channel(monkeypatch)
    _seed_watchlist()
    _seed_bars_and_indicators(iv_atm=0.30)
    quote_source, _ = _quote_source_factory(mid=175.0)

    def chain_source(_symbol: str) -> list[OptionSnapshotRecord]:
        # Current IV 0.32 → +6.7%, below the 0.20 threshold.
        return [
            OptionSnapshotRecord(
                symbol="AAPL",
                expiration=AS_OF + timedelta(days=30),
                strike=175.0,
                option_type="call",
                bid=None,
                ask=None,
                last=None,
                volume=None,
                open_interest=None,
                delta=None,
                gamma=None,
                theta=None,
                vega=None,
                iv=0.32,
            )
        ]

    with get_session() as session:
        run_intraday_pulse(
            session,
            quote_source=quote_source,
            chain_source=chain_source,
            as_of=AS_OF,
            now=RTH_NOW,
        )

    assert [c for c in fake.calls if c[0] == "iv_spike"] == []
    job = _latest_jobrun()
    result = job.result_json or {}
    assert result["iv_spike_fired"] == 0
    assert result["iv_spike_checked"] == 1


def test_iv_spike_throttled_within_interval(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_channel(monkeypatch)
    _seed_watchlist()
    _seed_bars_and_indicators(iv_atm=0.20)
    quote_source, _ = _quote_source_factory()

    chain_calls = 0

    def chain_source(_symbol: str) -> list[OptionSnapshotRecord]:
        nonlocal chain_calls
        chain_calls += 1
        return [
            OptionSnapshotRecord(
                symbol="AAPL",
                expiration=AS_OF + timedelta(days=30),
                strike=175.0,
                option_type="call",
                bid=None,
                ask=None,
                last=None,
                volume=None,
                open_interest=None,
                delta=None,
                gamma=None,
                theta=None,
                vega=None,
                iv=0.30,
            )
        ]

    # First tick: chain pulled.
    quote_source.anchor = RTH_NOW
    with get_session() as session:
        run_intraday_pulse(
            session,
            quote_source=quote_source,
            chain_source=chain_source,
            as_of=AS_OF,
            now=RTH_NOW,
        )
    assert chain_calls == 1

    # Second tick 5 minutes later (well under the 30m throttle): no chain pull.
    next_now = RTH_NOW + timedelta(minutes=5)
    quote_source.anchor = next_now
    with get_session() as session:
        run_intraday_pulse(
            session,
            quote_source=quote_source,
            chain_source=chain_source,
            as_of=AS_OF,
            now=next_now,
        )
    assert chain_calls == 1

    # Third tick after 31 minutes: throttle releases.
    later_now = RTH_NOW + timedelta(minutes=31)
    quote_source.anchor = later_now
    with get_session() as session:
        run_intraday_pulse(
            session,
            quote_source=quote_source,
            chain_source=chain_source,
            as_of=AS_OF,
            now=later_now,
        )
    assert chain_calls == 2
