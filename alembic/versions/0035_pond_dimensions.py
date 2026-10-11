"""pond width and length

Revision ID: 0035_pond_dimensions
Revises: 0034_prep_phase
Create Date: 2026-10-11

A pond's width and length in metres, so the farm layout can draw it to scale.
When both are set the API keeps area_m2 = width_m * length_m; area_m2 stays the
number prediction and the cycle results read. Existing ponds keep their area
and have no width or length until someone enters them.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0035_pond_dimensions"
down_revision: Union[str, None] = "0034_prep_phase"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ponds", sa.Column("width_m", sa.Numeric(8, 2), nullable=True))
    op.add_column("ponds", sa.Column("length_m", sa.Numeric(8, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("ponds", "length_m")
    op.drop_column("ponds", "width_m")
