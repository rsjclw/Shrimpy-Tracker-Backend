"""warehouses, inventory items and stock movements

Revision ID: 0023_inventory
Revises: 0022_feeding_additive_ids
Create Date: 2026-09-22

Every existing grid gets one "Main warehouse" so stock can be entered right
away; grids with several buildings add more from the inventory page.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_inventory"
down_revision: Union[str, None] = "0022_feeding_additive_ids"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "warehouses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("grid_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("grids.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("grid_id", "name", name="uq_warehouses_grid_name"),
    )
    op.create_index("ix_warehouses_grid_id", "warehouses", ["grid_id"])

    op.create_table(
        "inventory_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "warehouse_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("low_stock_level", sa.Numeric(14, 3)),
        sa.Column("location_note", sa.String(100)),
        sa.Column("feed_type_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("feed_types.id", ondelete="SET NULL")),
        sa.Column("additive_id", sa.Integer, sa.ForeignKey("feed_additives.id", ondelete="SET NULL")),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("warehouse_id", "name", name="uq_inventory_items_warehouse_name"),
        sa.UniqueConstraint("warehouse_id", "feed_type_id", name="uq_inventory_items_warehouse_feed_type"),
        sa.UniqueConstraint("warehouse_id", "additive_id", name="uq_inventory_items_warehouse_additive"),
    )
    op.create_index("ix_inventory_items_warehouse_id", "inventory_items", ["warehouse_id"])

    op.create_table(
        "inventory_movements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("delta", sa.Numeric(14, 3), nullable=False),
        sa.Column("quantity_after", sa.Numeric(14, 3), nullable=False),
        sa.Column("note", sa.Text),
        sa.Column("created_by", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_inventory_movements_item_id", "inventory_movements", ["item_id"])
    op.create_index("ix_inventory_movements_created_at", "inventory_movements", ["created_at"])

    op.execute(
        "INSERT INTO warehouses (id, grid_id, name) "
        "SELECT gen_random_uuid(), id, 'Main warehouse' FROM grids"
    )


def downgrade() -> None:
    op.drop_table("inventory_movements")
    op.drop_table("inventory_items")
    op.drop_table("warehouses")
