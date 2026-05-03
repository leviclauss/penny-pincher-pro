"""add positions.acquisition_source

Revision ID: e3701e1ff7c9
Revises: bbab0f8819f9
Create Date: 2026-05-02 21:32:54.290130

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e3701e1ff7c9"
down_revision: str | None = "bbab0f8819f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("positions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("acquisition_source", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("positions", schema=None) as batch_op:
        batch_op.drop_column("acquisition_source")
