"""add blind_feeding_target_abw_g to cycles

Revision ID: 0012_blind_feeding_target_abw
Revises: 0011_blind_feeding_templates
Branch Labels: None
Depends On: None

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_blind_feeding_target_abw"
down_revision: Union[str, None] = "0011_blind_feeding_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cycles",
        sa.Column("blind_feeding_target_abw_g", sa.Numeric(10, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cycles", "blind_feeding_target_abw_g")
