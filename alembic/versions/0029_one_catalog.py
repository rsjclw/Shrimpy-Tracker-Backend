"""one catalog: feed types and feed additives become formulas

Revision ID: 0029_one_catalog
Revises: 0028_product_price
Create Date: 2026-09-25

`feed_types` and `feed_additives` were two more catalogs alongside `products`,
each naming things the farm already had. A feed is a formula over products, an
additive is a formula over products, and a treatment is a formula over products -
the only difference is the category, which `products.category` already carries.

So both tables go, and with them the two bridges that existed only to reconcile
them: filling a warehouse "from catalog", and keeping catalog names in step.

The dose an additive carried moves onto the catalog entry.

Both catalogs are carried across first: every feed type and every additive
becomes a product, stocked at zero in the farm's warehouses, and each cycle's
`prediction_config.feed_plan` is repointed at the new ids. Only then are the
tables dropped, so a database that never had the inventory feature does not come
out the other side with an empty catalog.

Existing feedings keep the snapshots inside `feeding_sessions.feed_types` and
`.additives`: those rows embed the brand, price and dose they were logged with,
so history still reads. They are no longer live references to anything.

Not reversible in substance: downgrade rebuilds the tables, never the rows.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_one_catalog"
down_revision: Union[str, None] = "0028_product_price"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Grams per kg of feed, for a formula that is dosed into feed.
    op.add_column("products", sa.Column("default_dose_gr_per_kg", sa.Numeric(8, 3)))

    # --- carry both catalogs across before anything is dropped -------------
    #
    # A database that has never had the inventory feature has no products at
    # all, so without this the two tables would go and leave nothing behind.
    # Each entry becomes a product and is stocked at zero in every warehouse on
    # the farm, which keeps the "a product is held somewhere" rule true.

    # A feed type carries its price; its name is "Brand Type", as the app showed it.
    op.execute(
        """
        INSERT INTO products (id, farm_id, name, category, kind, base_unit, tracked, active,
                              price_per_unit, feed_type_id, notes)
        SELECT gen_random_uuid(), ft.farm_id, btrim(ft.brand || ' ' || ft.type), 'feed', 'product',
               'kg', true, true, ft.price_per_kg, ft.id, ft.notes
        FROM feed_types ft
        WHERE NOT EXISTS (
            SELECT 1 FROM products p
            WHERE p.farm_id = ft.farm_id
              AND (p.feed_type_id = ft.id OR lower(p.name) = lower(btrim(ft.brand || ' ' || ft.type)))
        )
        ON CONFLICT (farm_id, name) DO NOTHING
        """
    )

    # An additive carries its dose. Category "supplements" matches what the
    # "add from catalog" button used to create.
    op.execute(
        """
        INSERT INTO products (id, farm_id, name, category, kind, base_unit, tracked, active,
                              default_dose_gr_per_kg, additive_id)
        SELECT gen_random_uuid(), fa.farm_id, fa.name, 'supplements', 'product',
               'kg', true, true, fa.dosage_gr_per_kg, fa.id
        FROM feed_additives fa
        WHERE NOT EXISTS (
            SELECT 1 FROM products p
            WHERE p.farm_id = fa.farm_id
              AND (p.additive_id = fa.id OR lower(p.name) = lower(fa.name))
        )
        ON CONFLICT (farm_id, name) DO NOTHING
        """
    )

    # Doses for products that were already linked to an additive before this ran.
    op.execute(
        """
        UPDATE products p
        SET default_dose_gr_per_kg = fa.dosage_gr_per_kg
        FROM feed_additives fa
        WHERE fa.id = p.additive_id AND p.default_dose_gr_per_kg IS NULL
        """
    )

    # Stock every product at zero wherever the farm keeps things, so none is
    # left unheld. Receiving real amounts is a job for the inventory page.
    op.execute(
        """
        INSERT INTO inventory_items (id, warehouse_id, product_id, quantity)
        SELECT gen_random_uuid(), w.id, p.id, 0
        FROM products p
        JOIN grids g ON g.farm_id = p.farm_id
        JOIN warehouses w ON w.grid_id = g.id
        WHERE p.kind = 'product'
          AND NOT EXISTS (
              SELECT 1 FROM inventory_items i
              WHERE i.warehouse_id = w.id AND i.product_id = p.id
          )
        """
    )

    # A cycle's feed plan names a feed type; point it at the product instead.
    # `feed_type_id` is still read as a fallback, but rewriting means a plan
    # keeps working once that fallback eventually goes.
    op.execute(
        """
        UPDATE cycles c
        SET prediction_config = jsonb_set(
                c.prediction_config,
                '{feed_plan}',
                (
                    SELECT jsonb_agg(
                        CASE
                            WHEN p.id IS NOT NULL
                                THEN (row - 'feed_type_id') || jsonb_build_object('product_id', p.id::text)
                            ELSE row
                        END
                        ORDER BY ord
                    )
                    FROM jsonb_array_elements(c.prediction_config -> 'feed_plan')
                         WITH ORDINALITY AS t(row, ord)
                    LEFT JOIN products p
                           ON p.feed_type_id::text = (row ->> 'feed_type_id')
                )
            )
        WHERE c.prediction_config ? 'feed_plan'
          AND jsonb_typeof(c.prediction_config -> 'feed_plan') = 'array'
          AND jsonb_array_length(c.prediction_config -> 'feed_plan') > 0
        """
    )

    # --- now the old catalogs can go --------------------------------------
    op.drop_constraint("uq_products_farm_feed_type", "products", type_="unique")
    op.drop_constraint("uq_products_farm_additive", "products", type_="unique")
    op.drop_column("products", "feed_type_id")
    op.drop_column("products", "additive_id")

    op.drop_table("feed_additives")
    op.drop_table("feed_types")


def downgrade() -> None:
    op.create_table(
        "feed_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("farm_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("farms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("brand", sa.String(100), nullable=False),
        sa.Column("type", sa.String(100), nullable=False),
        sa.Column("price_per_kg", sa.Numeric(12, 2), nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "feed_additives",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("farm_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("farms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("dosage_gr_per_kg", sa.Numeric(8, 3)),
        sa.UniqueConstraint("farm_id", "name", name="uq_feed_additives_farm_name"),
    )
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
    op.drop_column("products", "default_dose_gr_per_kg")
