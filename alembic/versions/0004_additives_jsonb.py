"""change additives column to jsonb to store name + dosage per session

Revision ID: 0004_additives_jsonb
Revises: 0003_additive_dosages
Create Date: 2026-05-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0004_additives_jsonb"
down_revision: Union[str, None] = "0003_additive_dosages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("feeding_sessions", "additives")
    op.add_column(
        "feeding_sessions",
        sa.Column("additives", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("feeding_sessions", "additives")
    op.add_column(
        "feeding_sessions",
        sa.Column(
            "additives",
            sa.ARRAY(sa.String()),
            nullable=False,
            server_default="'{}'",
        ),
    )
