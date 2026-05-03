"""telegram inbound: alert_preferences.snooze_until + bot_state KV

Revision ID: 73dbb00734c5
Revises: e3701e1ff7c9
Create Date: 2026-05-03 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "73dbb00734c5"
down_revision: str | None = "e3701e1ff7c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("alert_preferences", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("snooze_until", sa.DateTime(timezone=True), nullable=True)
        )

    op.create_table(
        "bot_state",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("bot_state")
    with op.batch_alter_table("alert_preferences", schema=None) as batch_op:
        batch_op.drop_column("snooze_until")
