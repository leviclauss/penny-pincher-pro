"""add tickers.is_hidden

Revision ID: bbab0f8819f9
Revises: 516eb43cb94b
Create Date: 2026-05-01 21:21:14.781434

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "bbab0f8819f9"
down_revision: str | None = "516eb43cb94b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tickers", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_hidden",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    with op.batch_alter_table("tickers", schema=None) as batch_op:
        batch_op.alter_column("is_hidden", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("tickers", schema=None) as batch_op:
        batch_op.drop_column("is_hidden")
