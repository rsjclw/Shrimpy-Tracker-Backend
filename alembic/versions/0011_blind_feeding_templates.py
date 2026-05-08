"""add blind feeding templates

Revision ID: 0011_blind_feeding_templates
Revises: 0010_farms_and_memberships
Create Date: 2026-05-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0011_blind_feeding_templates"
down_revision: Union[str, None] = "0010_farms_and_memberships"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "blind_feeding_templates",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("farm_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("daily_feed_per_100k", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("farm_id", "name", name="uq_blind_feeding_templates_farm_name"),
    )
    op.create_index("ix_blind_feeding_templates_farm_id", "blind_feeding_templates", ["farm_id"])
    op.add_column(
        "cycles",
        sa.Column("blind_feeding_template_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_cycles_blind_feeding_template_id",
        "cycles",
        "blind_feeding_templates",
        ["blind_feeding_template_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_cycles_blind_feeding_template_id", "cycles", type_="foreignkey")
    op.drop_column("cycles", "blind_feeding_template_id")
    op.drop_index("ix_blind_feeding_templates_farm_id", table_name="blind_feeding_templates")
    op.drop_table("blind_feeding_templates")
