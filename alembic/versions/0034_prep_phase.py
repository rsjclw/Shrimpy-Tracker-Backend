"""pond preparation: a cycle can start before stocking

Revision ID: 0034_prep_phase
Revises: 0033_mortality
Create Date: 2026-10-04

A cycle is created when preparation starts (status "preparing") with the day
preparation began and a planned stocking day in start_date; population and
initial ABW are only known once the pond is stocked (POST /cycles/{id}/stock),
so they become nullable. Existing cycles keep their values.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0034_prep_phase"
down_revision: Union[str, None] = "0033_mortality"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cycles", sa.Column("prep_start_date", sa.Date(), nullable=True))
    op.alter_column("cycles", "initial_population", existing_type=sa.Integer(), nullable=True)
    op.alter_column("cycles", "initial_abw_g", existing_type=sa.Numeric(10, 4), nullable=True)


def downgrade() -> None:
    # A cycle that never got stocked has nothing to put back; it goes with the downgrade.
    op.execute("DELETE FROM cycles WHERE initial_population IS NULL OR initial_abw_g IS NULL")
    op.alter_column("cycles", "initial_abw_g", existing_type=sa.Numeric(10, 4), nullable=False)
    op.alter_column("cycles", "initial_population", existing_type=sa.Integer(), nullable=False)
    op.drop_column("cycles", "prep_start_date")
