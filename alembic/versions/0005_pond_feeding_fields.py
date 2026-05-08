"""add pond feeding planning fields

Revision ID: 0005_pond_feeding_fields
Revises: 0004_additives_jsonb
Create Date: 2026-05-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_pond_feeding_fields"
down_revision: Union[str, None] = "0004_additives_jsonb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ponds",
        sa.Column("maximum_daily_feed_capacity_kg", sa.Numeric(10, 3), nullable=True),
    )
    op.add_column(
        "ponds",
        sa.Column("carrying_capacity_kg_per_m3", sa.Numeric(10, 3), nullable=True),
    )
    op.add_column(
        "ponds",
        sa.Column(
            "feeding_index_increment",
            sa.Numeric(10, 3),
            nullable=False,
            server_default="0.010",
        ),
    )
    op.add_column(
        "ponds",
        sa.Column("maximum_feeding_index", sa.Numeric(10, 3), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ponds", "maximum_feeding_index")
    op.drop_column("ponds", "feeding_index_increment")
    op.drop_column("ponds", "carrying_capacity_kg_per_m3")
    op.drop_column("ponds", "maximum_daily_feed_capacity_kg")
