"""products, their units and recipes, and the link from treatments to stock

Revision ID: 0024_products
Revises: 0023_inventory
Create Date: 2026-09-23

A product is what the farm applies to a pond. A "mixture" product is a recipe
over other products and is never stocked itself, so applying one draws on
several inventory items at once. Movements gain a source so editing or deleting
a treatment can put its stock back.

Nothing here changes the existing inventory columns: `inventory_items.name`,
`category` and `unit` stay the source of truth for the inventory page, and
`product_id` is an additive link for items that take part in treatments.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_products"
down_revision: Union[str, None] = "0023_inventory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("farm_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("farms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="stocked"),
        sa.Column("base_unit", sa.String(30), nullable=False),
        sa.Column("tracked", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("farm_id", "name", name="uq_products_farm_name"),
    )
    op.create_index("ix_products_farm_id", "products", ["farm_id"])

    op.create_table(
        "product_units",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("factor_to_base", sa.Numeric(18, 6), nullable=False),
        sa.UniqueConstraint("product_id", "unit", name="uq_product_units_product_unit"),
    )
    op.create_index("ix_product_units_product_id", "product_units", ["product_id"])

    op.create_table(
        "product_components",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False
        ),
        # RESTRICT so deleting an ingredient cannot quietly shrink a recipe.
        sa.Column(
            "component_product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Numeric(14, 4), nullable=False),
        sa.UniqueConstraint("product_id", "component_product_id", name="uq_product_components_pair"),
    )
    op.create_index("ix_product_components_product_id", "product_components", ["product_id"])
    op.create_index("ix_product_components_component_product_id", "product_components", ["component_product_id"])

    op.add_column(
        "inventory_items",
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="SET NULL")),
    )
    op.create_unique_constraint(
        "uq_inventory_items_warehouse_product", "inventory_items", ["warehouse_id", "product_id"]
    )

    op.add_column("inventory_movements", sa.Column("source_type", sa.String(20)))
    op.add_column("inventory_movements", sa.Column("source_id", postgresql.UUID(as_uuid=True)))
    op.create_index(
        "ix_inventory_movements_source", "inventory_movements", ["source_type", "source_id"]
    )

    op.add_column(
        "treatments",
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("warehouses.id", ondelete="SET NULL")),
    )
    op.add_column(
        "treatments",
        sa.Column("items", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("treatments", "items")
    op.drop_column("treatments", "warehouse_id")
    op.drop_index("ix_inventory_movements_source", table_name="inventory_movements")
    op.drop_column("inventory_movements", "source_id")
    op.drop_column("inventory_movements", "source_type")
    op.drop_constraint("uq_inventory_items_warehouse_product", "inventory_items", type_="unique")
    op.drop_column("inventory_items", "product_id")
    op.drop_table("product_components")
    op.drop_table("product_units")
    op.drop_table("products")
