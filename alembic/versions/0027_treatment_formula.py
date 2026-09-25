"""the applied kind is a treatment formula

Revision ID: 0027_formula
Revises: 0026_product_kinds
Create Date: 2026-09-23

"treatment" collided with the `treatments` table, which is the log of what was
applied to a pond on a day. The catalog entry is the *formula* for a treatment -
Lactobacillus, Mix A - so that is what it is called.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0027_formula"
down_revision: Union[str, None] = "0026_product_kinds"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE products SET kind = 'formula' WHERE kind = 'treatment'")


def downgrade() -> None:
    op.execute("UPDATE products SET kind = 'treatment' WHERE kind = 'formula'")
