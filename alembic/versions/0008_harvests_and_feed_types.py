"""add harvests and feed type mixes

Revision ID: 0008_harvests_and_feed_types
Revises: 0007_abw_sample_time
Create Date: 2026-05-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0008_harvests_and_feed_types"
down_revision: Union[str, None] = "0007_abw_sample_time"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cycles",
        sa.Column("feed_types", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "feeding_sessions",
        sa.Column("feed_types", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.create_table(
        "harvests",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("daily_log_id", UUID(as_uuid=True), nullable=False),
        sa.Column("harvest_time", sa.Time(), nullable=False),
        sa.Column("biomass_kg", sa.Numeric(10, 3), nullable=False),
        sa.Column("sampled_abw_g", sa.Numeric(10, 4), nullable=False),
        sa.Column("price_per_kg", sa.Numeric(12, 2), nullable=False),
        sa.Column("estimated_count", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["daily_log_id"], ["daily_logs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_harvests_daily_log_id", "harvests", ["daily_log_id"])


def downgrade() -> None:
    op.drop_index("ix_harvests_daily_log_id", table_name="harvests")
    op.drop_table("harvests")
    op.drop_column("feeding_sessions", "feed_types")
    op.drop_column("cycles", "feed_types")
