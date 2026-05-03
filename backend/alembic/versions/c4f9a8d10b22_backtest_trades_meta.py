"""backtest_trades: add meta JSON column

Revision ID: c4f9a8d10b22
Revises: e6d88fcf0798
Create Date: 2026-05-03 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4f9a8d10b22"
down_revision: str | None = "e6d88fcf0798"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backtest_trades", schema=None) as batch_op:
        batch_op.add_column(sa.Column("meta", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("backtest_trades", schema=None) as batch_op:
        batch_op.drop_column("meta")
