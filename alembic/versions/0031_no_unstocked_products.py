"""a product exists because a warehouse holds it

Revision ID: 0031_stocked_only
Revises: 0030_pond_warehouse
Create Date: 2026-09-25

A product with no stock row anywhere was invisible: the inventory page lists what
a warehouse holds, and settings lists formulas, so it appeared in neither. They
came about because the catalog could be written to directly.

Creating a product now only happens by stocking it, and deleting its last stock
row deletes it. This clears the ones already stranded. Formulas are untouched -
they are never stocked by design.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0031_stocked_only"
down_revision: Union[str, None] = "0030_pond_warehouse"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Anything an existing formula still names is kept: dropping it would quietly
    # shrink that recipe. Those show up as unstocked ingredients to fix by hand.
    op.execute(
        """
        DELETE FROM products p
        WHERE p.kind = 'product'
          AND NOT EXISTS (SELECT 1 FROM inventory_items i WHERE i.product_id = p.id)
          AND NOT EXISTS (SELECT 1 FROM product_components c WHERE c.component_product_id = p.id)
        """
    )


def downgrade() -> None:
    # The rows are gone; nothing to put back.
    pass
