"""pond layout: where a pond stands on its grid's drawing

Revision ID: 0039_pond_layout
Revises: 0038_blower_kind
Create Date: 2026-10-12

x_m / y_m are the pond's middle on the grid's layout drawing, in metres east and south
of the drawing's origin (null: not drawn yet). rotation turns it clockwise, 0-359.
Equipment keeps its own spot in equipment_placements.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0039_pond_layout"
down_revision: Union[str, None] = "0038_blower_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ponds", sa.Column("x_m", sa.Numeric(8, 2), nullable=True))
    op.add_column("ponds", sa.Column("y_m", sa.Numeric(8, 2), nullable=True))
    op.add_column("ponds", sa.Column("rotation", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint("ck_ponds_layout_xy", "ponds", "(x_m IS NULL) = (y_m IS NULL)")
    op.create_check_constraint("ck_ponds_rotation", "ponds", "rotation >= 0 AND rotation <= 359")


def downgrade() -> None:
    op.drop_constraint("ck_ponds_rotation", "ponds", type_="check")
    op.drop_constraint("ck_ponds_layout_xy", "ponds", type_="check")
    op.drop_column("ponds", "rotation")
    op.drop_column("ponds", "y_m")
    op.drop_column("ponds", "x_m")
