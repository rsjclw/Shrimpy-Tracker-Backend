"""no stored default dose; the dose unit follows the product

Revision ID: 0032_dose_per_kg
Revises: 0031_stocked_only
Create Date: 2026-09-25

A default dose on the catalog entry was a second source of truth next to what a
cycle is actually dosing. The last dose used in the cycle already carries
forward, which covers the repeat case, so the stored default goes and the first
dose of a cycle is typed in.

The dose unit is no longer always "grams per kg of feed". It follows what the
product is counted in - grams for a mass, millilitres for a volume - so a liquid
is dosed in mL/kg instead of nonsense grams.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032_dose_per_kg"
down_revision: Union[str, None] = "0031_stocked_only"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("products", "default_dose_gr_per_kg")


def downgrade() -> None:
    op.add_column("products", sa.Column("default_dose_gr_per_kg", sa.Numeric(8, 3)))
