"""add farm tenancy and memberships

Revision ID: 0010_farms_and_memberships
Revises: 0009_farm_feed_catalogs
Create Date: 2026-05-05

"""
from typing import Sequence, Union
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0010_farms_and_memberships"
down_revision: Union[str, None] = "0009_farm_feed_catalogs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "farms",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "farm_memberships",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("farm_id", UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("farm_id", "email", name="uq_farm_memberships_farm_email"),
    )
    op.create_index("ix_farm_memberships_email", "farm_memberships", ["email"])
    op.create_index("ix_farm_memberships_user_id", "farm_memberships", ["user_id"])

    initial_farm_id = uuid.uuid4()
    op.execute(
        sa.text("INSERT INTO farms (id, name) VALUES (:id, :name)").bindparams(
            id=initial_farm_id, name="Default Farm"
        )
    )

    for table_name in ("grids", "feed_types", "feed_additives"):
        op.add_column(table_name, sa.Column("farm_id", UUID(as_uuid=True), nullable=True))
        op.execute(
            sa.text(f"UPDATE {table_name} SET farm_id = :farm_id").bindparams(
                farm_id=initial_farm_id
            )
        )
        op.alter_column(table_name, "farm_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table_name}_farm_id_farms",
            table_name,
            "farms",
            ["farm_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index(f"ix_{table_name}_farm_id", table_name, ["farm_id"])

    op.drop_constraint("feed_additives_name_key", "feed_additives", type_="unique")
    op.create_unique_constraint(
        "uq_feed_additives_farm_name",
        "feed_additives",
        ["farm_id", "name"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_feed_additives_farm_name", "feed_additives", type_="unique")
    op.create_unique_constraint("feed_additives_name_key", "feed_additives", ["name"])

    for table_name in ("feed_additives", "feed_types", "grids"):
        op.drop_index(f"ix_{table_name}_farm_id", table_name=table_name)
        op.drop_constraint(f"fk_{table_name}_farm_id_farms", table_name, type_="foreignkey")
        op.drop_column(table_name, "farm_id")

    op.drop_index("ix_farm_memberships_user_id", table_name="farm_memberships")
    op.drop_index("ix_farm_memberships_email", table_name="farm_memberships")
    op.drop_table("farm_memberships")
    op.drop_table("farms")
