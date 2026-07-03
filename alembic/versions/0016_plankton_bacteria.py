"""add plankton and bacteria water parameters

Revision ID: 0016_plankton_bacteria
Revises: 0015_drop_legacy_cycle_inputs
Create Date: 2026-06-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_plankton_bacteria"
down_revision: Union[str, None] = "0015_drop_legacy_cycle_inputs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BIOLOGY_COLUMNS = (
    "plankton_ga",
    "plankton_bga",
    "plankton_diatom",
    "plankton_yga",
    "plankton_eugle",
    "plankton_dino",
    "plankton_zoo",
    "plankton_protozoa",
    "yellow_vibrio",
    "green_vibrio",
    "black_vibrio",
    "tbc",
)


def upgrade() -> None:
    existing_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("water_parameters")}
    for column in BIOLOGY_COLUMNS:
        if column not in existing_columns:
            op.add_column("water_parameters", sa.Column(column, sa.Numeric(14, 2)))


def downgrade() -> None:
    existing_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("water_parameters")}
    for column in reversed(BIOLOGY_COLUMNS):
        if column in existing_columns:
            op.drop_column("water_parameters", column)
