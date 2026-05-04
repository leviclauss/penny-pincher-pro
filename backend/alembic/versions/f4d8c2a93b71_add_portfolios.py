"""add portfolios + positions.portfolio_id

Revision ID: f4d8c2a93b71
Revises: d8a1f3b27e91
Create Date: 2026-05-03 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f4d8c2a93b71"
down_revision: str | None = "d8a1f3b27e91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "portfolios",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_portfolios_name"),
    )
    with op.batch_alter_table("positions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("portfolio_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_positions_portfolio_id_portfolios",
            "portfolios",
            ["portfolio_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_positions_portfolio_id", ["portfolio_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("positions", schema=None) as batch_op:
        batch_op.drop_index("ix_positions_portfolio_id")
        batch_op.drop_constraint("fk_positions_portfolio_id_portfolios", type_="foreignkey")
        batch_op.drop_column("portfolio_id")
    op.drop_table("portfolios")
