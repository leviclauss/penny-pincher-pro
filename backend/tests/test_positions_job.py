"""End-to-end test for the daily position-management scheduler job.

Seeds underlyings + an option chain, opens a short put with a snapshot that
will trigger ``pct_max_profit``, runs the job, and verifies a snapshot row +
job_runs row + dispatched alert exist.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import select

from alembic import command
from db import get_session
from db.models.market import BarDaily, OptionsSnapshot, Ticker
from db.models.positions import PositionSnapshot
from db.models.system import JobRun
from positions import state_machine as sm
from scheduler.jobs.positions import JOB_NAME, run_position_management


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_path = tmp_path / "positions_job.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")

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

    yield

    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()


def test_job_writes_snapshot_jobrun_and_dispatches_alert(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seed ticker + bar + option chain (mid = 0.50, well below 50% of 3.00 credit).
    with get_session() as session:
        session.add(
            Ticker(
                symbol="AAPL",
                is_active=True,
                is_hidden=False,
                added_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        session.flush()
        session.add(
            BarDaily(
                symbol="AAPL",
                date=date(2026, 5, 5),
                open=180.0,
                high=180.0,
                low=180.0,
                close=180.0,
                volume=1_000_000,
            )
        )
        session.add(
            OptionsSnapshot(
                symbol="AAPL",
                expiration=date(2026, 6, 19),
                strike=170.0,
                option_type="put",
                bid=0.40,
                ask=0.60,
                last=0.50,
                delta=-0.10,
            )
        )

    with get_session() as session:
        sm.open_short_put(
            session,
            sm.OpenShortPutInput(
                symbol="AAPL",
                expiration=date(2026, 6, 19),
                strike=170.0,
                contracts=1,
                credit=3.00,
                opened_on=date(2026, 5, 1),
            ),
        )

    fired: list[tuple[str, dict[str, object]]] = []

    def fake_dispatch(alert_type: str, payload: dict[str, object], **_: object) -> None:
        fired.append((alert_type, payload))

    monkeypatch.setattr("alerts.dispatcher.dispatch", fake_dispatch)

    with get_session() as session:
        run_position_management(session, as_of=date(2026, 5, 5))

    with get_session() as session:
        snap_count = session.execute(select(PositionSnapshot)).scalars().all()
        assert len(snap_count) == 1

        job = (
            session.execute(
                select(JobRun).where(JobRun.job_name == JOB_NAME).order_by(JobRun.id.desc())
            )
            .scalars()
            .first()
        )
        assert job is not None
        assert job.status == "success"
        result = job.result_json or {}
        assert result["snapshots"] == 1
        assert result["positions"] == 1
        assert result["triggers"] >= 1
        assert result["alerts_fired"] >= 1

    rules = {payload["rule"] for _, payload in fired}
    assert "pct_max_profit" in rules
    # Sanity-check JSON serializability of payload (matches dispatcher contract).
    for _, payload in fired:
        json.dumps(payload)
