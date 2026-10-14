"""receive details: how a delivery came, what it cost, who sold it

Revision ID: 0040_receive_details
Revises: 0039_pond_layout
Create Date: 2026-10-14

Pack sizes and prices change between deliveries, so they are recorded on the receive
itself: pack_count packs of pack_unit, pack_size base units each, total_cost for the
lot, and the supplier. The product's unit and price stay as the defaults the latest
delivery sets.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040_receive_details"
down_revision: Union[str, None] = "0039_pond_layout"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("inventory_movements", sa.Column("pack_unit", sa.String(30), nullable=True))
    op.add_column("inventory_movements", sa.Column("pack_size", sa.Numeric(18, 6), nullable=True))
    op.add_column("inventory_movements", sa.Column("pack_count", sa.Numeric(14, 3), nullable=True))
    op.add_column("inventory_movements", sa.Column("total_cost", sa.Numeric(16, 2), nullable=True))
    op.add_column("inventory_movements", sa.Column("supplier", sa.String(150), nullable=True))
    op.create_check_constraint(
        "ck_inventory_movements_pack",
        "inventory_movements",
        "(pack_unit IS NULL) = (pack_size IS NULL) AND (pack_unit IS NULL) = (pack_count IS NULL)",
    )
    op.create_check_constraint(
        "ck_inventory_movements_receive_details",
        "inventory_movements",
        "kind = 'receive' OR (pack_unit IS NULL AND total_cost IS NULL AND supplier IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_inventory_movements_receive_details", "inventory_movements", type_="check")
    op.drop_constraint("ck_inventory_movements_pack", "inventory_movements", type_="check")
    op.drop_column("inventory_movements", "supplier")
    op.drop_column("inventory_movements", "total_cost")
    op.drop_column("inventory_movements", "pack_count")
    op.drop_column("inventory_movements", "pack_size")
    op.drop_column("inventory_movements", "pack_unit")
