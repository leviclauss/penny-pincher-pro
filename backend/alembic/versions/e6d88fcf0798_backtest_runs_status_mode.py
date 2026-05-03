"""backtest_runs: add mode + status + error_message

Revision ID: e6d88fcf0798
Revises: 73dbb00734c5
Create Date: 2026-05-03 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e6d88fcf0798"
down_revision: str | None = "73dbb00734c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backtest_runs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "mode",
                sa.String(length=16),
                nullable=False,
                server_default="filter",
            )
        )
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default="completed",
            )
        )
        batch_op.add_column(sa.Column("error_message", sa.Text(), nullable=True))
        batch_op.create_index(
            "ix_backtest_runs_status", ["status"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("backtest_runs", schema=None) as batch_op:
        batch_op.drop_index("ix_backtest_runs_status")
        batch_op.drop_column("error_message")
        batch_op.drop_column("status")
        batch_op.drop_column("mode")
