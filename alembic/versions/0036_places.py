"""places: reservoirs and bore wells

Revision ID: 0036_places
Revises: 0035_pond_dimensions
Create Date: 2026-10-11

Reservoirs and bore wells per grid, for the farm layout and its equipment
(pumps move water from a well to a reservoir; reservoirs have paddlewheels).
Not ponds: they have no cycles, and must not show up in pond lists. A
reservoir has a width and length in metres; a bore well has no size.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036_places"
down_revision: Union[str, None] = "0035_pond_dimensions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "places",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("grid_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("grids.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("width_m", sa.Numeric(8, 2), nullable=True),
        sa.Column("length_m", sa.Numeric(8, 2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("grid_id", "name", name="uq_places_grid_name"),
        sa.CheckConstraint("kind IN ('reservoir', 'bore_well')", name="ck_places_kind"),
    )
    op.create_index("ix_places_grid_id", "places", ["grid_id"])


def downgrade() -> None:
    op.drop_index("ix_places_grid_id", table_name="places")
    op.drop_table("places")
