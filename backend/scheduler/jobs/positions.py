"""Daily position-management job.

Runs after the evening data pipeline so the snapshot pass sees today's bars
and option chain. Order: snapshot every open position, then evaluate
management rules and fan out alerts. Wraps in ``job_run`` so each execution
lands in ``job_runs`` with the metrics we care about.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from core.logging import get_logger
from core.time import utcnow
from positions.management import ManagementConfig, fire_triggers, run_management_pass
from positions.snapshot import run_snapshot_pass
from scheduler.context import job_run

log = get_logger(__name__)

JOB_NAME = "position_management"


def run_position_management(
    session: Session,
    *,
    as_of: date | None = None,
    config: ManagementConfig | None = None,
) -> None:
    """Run the daily snapshot + management-rule pass once."""
    today = as_of or utcnow().date()
    with job_run(session, JOB_NAME) as ctx:
        snap = run_snapshot_pass(session, as_of=today)
        triggers = run_management_pass(session, config=config, today=today)
        fire_result = fire_triggers(session, triggers)
        ctx.set_result(
            positions=snap.positions_snapshotted,
            snapshots=snap.snapshots_written,
            skipped=snap.skipped_no_underlying,
            triggers=len(triggers),
            alerts_fired=fire_result.dispatched,
            alerts_suppressed=fire_result.suppressed,
            as_of=today.isoformat(),
        )
