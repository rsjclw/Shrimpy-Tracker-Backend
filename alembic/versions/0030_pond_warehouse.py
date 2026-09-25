"""which warehouse a pond draws from

Revision ID: 0030_pond_warehouse
Revises: 0029_one_catalog
Create Date: 2026-09-25

A feeding or a treatment on a pond has to come out of somewhere. Asking every
time would be miserable for feed, which is logged many times a day and also
arrives from the ingest and import paths where there is nobody to ask.

So a pond names the warehouse it draws from. It is a column on the pond rather
than a join table on purpose: a pond with two warehouses would put the ambiguity
straight back, and the whole point is that it can be resolved without asking.
Chosen from the warehouse's side in the UI ("which ponds take from here").

Null means not assigned: those ponds simply do not move stock.

Every grid currently has exactly one warehouse, so each pond is pointed at its
own grid's warehouse to start with.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030_pond_warehouse"
down_revision: Union[str, None] = "0029_one_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ponds",
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("warehouses.id", ondelete="SET NULL")),
    )
    # Only where the grid has exactly one, so nothing is guessed.
    op.execute(
        """
        UPDATE ponds p
        SET warehouse_id = w.id
        FROM warehouses w
        WHERE w.grid_id = p.grid_id
          AND (SELECT count(*) FROM warehouses w2 WHERE w2.grid_id = p.grid_id) = 1
        """
    )


def downgrade() -> None:
    op.drop_column("ponds", "warehouse_id")
