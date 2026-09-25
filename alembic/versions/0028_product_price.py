"""what a product costs, per its own unit

Revision ID: 0028_product_price
Revises: 0027_formula
Create Date: 2026-09-24

Price belongs to the thing you buy, in the unit it is counted in - kg of feed,
L of a probiotic, pcs of a spare. From here a formula's cost is a sum over its
ingredients, which nothing could work out before.

Feedings already snapshot the price they were logged at, inside
`feeding_sessions.feed_types`, so editing a price here never rewrites history.

Existing `feed_types.price_per_kg` is left alone: those rows stay readable while
feed moves over to the catalog.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028_product_price"
down_revision: Union[str, None] = "0027_formula"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("price_per_unit", sa.Numeric(14, 2)))
    # Products created from a feed type can start from the price it carried.
    op.execute(
        """
        UPDATE products p
        SET price_per_unit = ft.price_per_kg
        FROM feed_types ft
        WHERE ft.id = p.feed_type_id AND p.base_unit = 'kg'
        """
    )


def downgrade() -> None:
    op.drop_column("products", "price_per_unit")
