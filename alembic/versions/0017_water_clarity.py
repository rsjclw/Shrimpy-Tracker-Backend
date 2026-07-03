"""add water clarity water parameters

Revision ID: 0017_water_clarity
Revises: 0016_plankton_bacteria
Create Date: 2026-07-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_water_clarity"
down_revision: Union[str, None] = "0016_plankton_bacteria"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


WATER_CLARITY_COLUMNS = ("water_clarity_am", "water_clarity_pm")


def upgrade() -> None:
    existing_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("water_parameters")}
    for column in WATER_CLARITY_COLUMNS:
        if column not in existing_columns:
            op.add_column("water_parameters", sa.Column(column, sa.Numeric(8, 2)))


def downgrade() -> None:
    existing_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("water_parameters")}
    for column in reversed(WATER_CLARITY_COLUMNS):
        if column in existing_columns:
            op.drop_column("water_parameters", column)
