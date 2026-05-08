"""move feed types to farm catalog

Revision ID: 0009_farm_feed_catalogs
Revises: 0008_harvests_and_feed_types
Create Date: 2026-05-05

"""
from decimal import Decimal
from typing import Sequence, Union
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0009_farm_feed_catalogs"
down_revision: Union[str, None] = "0008_harvests_and_feed_types"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feed_types",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("brand", sa.String(length=100), nullable=False),
        sa.Column("type", sa.String(length=100), nullable=False),
        sa.Column("price_per_kg", sa.Numeric(12, 2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    bind = op.get_bind()
    existing = bind.execute(sa.text("SELECT feed_types FROM cycles")).scalars().all()
    seen: set[tuple[str, str, Decimal, str | None]] = set()
    rows: list[dict[str, object]] = []
    for feed_types in existing:
        for item in feed_types or []:
            brand = str(item.get("brand") or "").strip()
            feed_type = str(item.get("type") or "").strip()
            price = item.get("price_per_kg")
            if not brand or not feed_type or price in (None, ""):
                continue
            notes = item.get("notes")
            key = (brand, feed_type, Decimal(str(price)), str(notes).strip() if notes else None)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "brand": brand,
                    "type": feed_type,
                    "price_per_kg": key[2],
                    "notes": key[3],
                }
            )

    if rows:
        feed_types_table = sa.table(
            "feed_types",
            sa.column("id", UUID(as_uuid=True)),
            sa.column("brand", sa.String),
            sa.column("type", sa.String),
            sa.column("price_per_kg", sa.Numeric),
            sa.column("notes", sa.Text),
        )
        op.bulk_insert(feed_types_table, rows)

    op.drop_column("cycles", "feed_types")


def downgrade() -> None:
    op.add_column(
        "cycles",
        sa.Column("feed_types", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.drop_table("feed_types")
