"""add options_historical

Revision ID: d8a1f3b27e91
Revises: c4f9a8d10b22
Create Date: 2026-05-04 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d8a1f3b27e91"
down_revision: str | None = "c4f9a8d10b22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "options_historical",
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("expiration", sa.Date(), nullable=False),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("option_type", sa.String(length=4), nullable=False),
        sa.Column("open", sa.Float(), nullable=True),
        sa.Column("high", sa.Float(), nullable=True),
        sa.Column("low", sa.Float(), nullable=True),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("volume", sa.Integer(), nullable=True),
        sa.Column("open_interest", sa.Integer(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["symbol"], ["tickers.symbol"]),
        sa.PrimaryKeyConstraint("symbol", "as_of", "expiration", "strike", "option_type"),
    )
    op.create_index(
        "ix_options_historical_symbol_as_of",
        "options_historical",
        ["symbol", "as_of"],
    )
    op.create_index(
        "ix_options_historical_symbol_expiration_as_of",
        "options_historical",
        ["symbol", "expiration", "as_of"],
    )


def downgrade() -> None:
    op.drop_index("ix_options_historical_symbol_expiration_as_of", "options_historical")
    op.drop_index("ix_options_historical_symbol_as_of", "options_historical")
    op.drop_table("options_historical")
