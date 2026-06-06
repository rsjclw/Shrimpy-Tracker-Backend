"""store harvest total price

Revision ID: 0013_harvest_total_price
Revises: 0012_blind_feeding_target_abw
Create Date: 2026-06-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_harvest_total_price"
down_revision: Union[str, None] = "0012_blind_feeding_target_abw"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("harvests", sa.Column("total_price", sa.Numeric(14, 2), nullable=True))
    op.execute("UPDATE harvests SET total_price = price_per_kg * biomass_kg")
    op.alter_column("harvests", "total_price", nullable=False)
    op.drop_column("harvests", "price_per_kg")


def downgrade() -> None:
    op.add_column("harvests", sa.Column("price_per_kg", sa.Numeric(12, 2), nullable=True))
    op.execute(
        """
        UPDATE harvests
        SET price_per_kg = CASE
            WHEN biomass_kg > 0 THEN total_price / biomass_kg
            ELSE 0
        END
        """
    )
    op.alter_column("harvests", "price_per_kg", nullable=False)
    op.drop_column("harvests", "total_price")
