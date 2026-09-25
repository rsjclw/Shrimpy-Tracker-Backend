"""inventory items become pure stock; identity moves to products

Revision ID: 0025_stock_is_pure
Revises: 0024_products
Create Date: 2026-09-23

Until now the same thing was described twice: `products.name/category/base_unit`
and a copy on every `inventory_items` row. A stock row now says only *where* and
*how much*; what the thing is lives on the product it points at.

The catalog links move with the identity: `feed_type_id` and `additive_id` go
from the stock row to the product, so a feed type is one product on the farm
rather than one item per warehouse.

Existing stock is carried over: every distinct item name in a farm becomes a
product, and the rows that named it point at it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_stock_is_pure"
down_revision: Union[str, None] = "0024_products"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. The catalog links belong to the identity, not to each warehouse's row.
    op.add_column(
        "products",
        sa.Column("feed_type_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("feed_types.id", ondelete="SET NULL")),
    )
    op.add_column(
        "products",
        sa.Column("additive_id", sa.Integer, sa.ForeignKey("feed_additives.id", ondelete="SET NULL")),
    )
    op.create_unique_constraint("uq_products_farm_feed_type", "products", ["farm_id", "feed_type_id"])
    op.create_unique_constraint("uq_products_farm_additive", "products", ["farm_id", "additive_id"])

    # 2. One product per distinct item name in a farm. MIN/MAX collapse the rare
    #    case of the same name stocked under a different unit in two warehouses.
    op.execute(
        """
        INSERT INTO products (id, farm_id, name, category, kind, base_unit, tracked, active,
                              feed_type_id, additive_id)
        -- Postgres has no max(uuid), so take the first non-null feed type instead.
        SELECT gen_random_uuid(), g.farm_id, i.name, MIN(i.category), 'stocked', MIN(i.unit),
               true, true,
               (array_agg(i.feed_type_id) FILTER (WHERE i.feed_type_id IS NOT NULL))[1],
               MAX(i.additive_id)
        FROM inventory_items i
        JOIN warehouses w ON w.id = i.warehouse_id
        JOIN grids g ON g.id = w.grid_id
        WHERE i.product_id IS NULL
        GROUP BY g.farm_id, i.name
        ON CONFLICT (farm_id, name) DO NOTHING
        """
    )

    # 3. Point every stock row at its product.
    op.execute(
        """
        UPDATE inventory_items i
        SET product_id = p.id
        FROM warehouses w
        JOIN grids g ON g.id = w.grid_id
        JOIN products p ON p.farm_id = g.farm_id
        WHERE w.id = i.warehouse_id
          AND p.name = i.name
          AND i.product_id IS NULL
        """
    )

    # 4. A stock row with no product cannot say what it is, so it cannot stay.
    #    The step above covers every row that had a name; this is belt and braces.
    op.execute("DELETE FROM inventory_items WHERE product_id IS NULL")

    # 5. Identity now lives in one place only.
    op.drop_constraint("uq_inventory_items_warehouse_name", "inventory_items", type_="unique")
    op.drop_constraint("uq_inventory_items_warehouse_feed_type", "inventory_items", type_="unique")
    op.drop_constraint("uq_inventory_items_warehouse_additive", "inventory_items", type_="unique")
    op.alter_column("inventory_items", "product_id", nullable=False)
    # RESTRICT, not SET NULL: stock that no longer knows what it is would be worse
    # than refusing to delete a product while a warehouse still holds some.
    op.drop_constraint("inventory_items_product_id_fkey", "inventory_items", type_="foreignkey")
    op.create_foreign_key(
        "inventory_items_product_id_fkey", "inventory_items", "products", ["product_id"], ["id"], ondelete="RESTRICT"
    )
    op.drop_column("inventory_items", "name")
    op.drop_column("inventory_items", "category")
    op.drop_column("inventory_items", "unit")
    op.drop_column("inventory_items", "feed_type_id")
    op.drop_column("inventory_items", "additive_id")


def downgrade() -> None:
    op.add_column("inventory_items", sa.Column("name", sa.String(150)))
    op.add_column("inventory_items", sa.Column("category", sa.String(30)))
    op.add_column("inventory_items", sa.Column("unit", sa.String(30)))
    op.add_column(
        "inventory_items",
        sa.Column("feed_type_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("feed_types.id", ondelete="SET NULL")),
    )
    op.add_column(
        "inventory_items",
        sa.Column("additive_id", sa.Integer, sa.ForeignKey("feed_additives.id", ondelete="SET NULL")),
    )
    op.execute(
        """
        UPDATE inventory_items i
        SET name = p.name, category = p.category, unit = p.base_unit,
            feed_type_id = p.feed_type_id, additive_id = p.additive_id
        FROM products p
        WHERE p.id = i.product_id
        """
    )
    op.alter_column("inventory_items", "name", nullable=False)
    op.alter_column("inventory_items", "category", nullable=False)
    op.alter_column("inventory_items", "unit", nullable=False)
    op.drop_constraint("inventory_items_product_id_fkey", "inventory_items", type_="foreignkey")
    op.create_foreign_key(
        "inventory_items_product_id_fkey", "inventory_items", "products", ["product_id"], ["id"], ondelete="SET NULL"
    )
    op.alter_column("inventory_items", "product_id", nullable=True)
    op.create_unique_constraint("uq_inventory_items_warehouse_name", "inventory_items", ["warehouse_id", "name"])
    op.create_unique_constraint(
        "uq_inventory_items_warehouse_feed_type", "inventory_items", ["warehouse_id", "feed_type_id"]
    )
    op.create_unique_constraint(
        "uq_inventory_items_warehouse_additive", "inventory_items", ["warehouse_id", "additive_id"]
    )
    op.drop_constraint("uq_products_farm_additive", "products", type_="unique")
    op.drop_constraint("uq_products_farm_feed_type", "products", type_="unique")
    op.drop_column("products", "additive_id")
    op.drop_column("products", "feed_type_id")
