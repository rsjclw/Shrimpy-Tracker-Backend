"""name the two catalog kinds what they are: product and treatment

Revision ID: 0026_product_kinds
Revises: 0025_stock_is_pure
Create Date: 2026-09-23

"stocked" and "mixture" described the storage, not the thing. Biolacto is the
**product** - it has a maker, a label and a price. Lactobacillus is the
**treatment**: what actually goes in the pond, defined by what it is made of.
A treatment is never stocked; applying it draws on the products in it.

This is the catalog's `kind` column only. The `treatments` table is a different
thing and is untouched: it is the log of what was applied to a pond on a day.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0026_product_kinds"
down_revision: Union[str, None] = "0025_stock_is_pure"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE products SET kind = 'product' WHERE kind = 'stocked'")
    op.execute("UPDATE products SET kind = 'treatment' WHERE kind = 'mixture'")
    op.alter_column("products", "kind", server_default="product")


def downgrade() -> None:
    op.execute("UPDATE products SET kind = 'stocked' WHERE kind = 'product'")
    op.execute("UPDATE products SET kind = 'mixture' WHERE kind = 'treatment'")
    op.alter_column("products", "kind", server_default="stocked")
