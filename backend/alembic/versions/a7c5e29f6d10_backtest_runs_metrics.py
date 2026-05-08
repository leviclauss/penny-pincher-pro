"""backtest_runs: add metrics_json JSON column

Revision ID: a7c5e29f6d10
Revises: f4d8c2a93b71
Create Date: 2026-05-07 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c5e29f6d10"
down_revision: str | None = "f4d8c2a93b71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backtest_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("metrics_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("backtest_runs", schema=None) as batch_op:
        batch_op.drop_column("metrics_json")
