"""add default feed time to ponds

Revision ID: 0021_pond_feed_time
Revises: 0020_users
Create Date: 2026-09-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_pond_feed_time"
down_revision: Union[str, None] = "0020_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ponds",
        sa.Column(
            "default_feed_time",
            sa.Time(),
            nullable=False,
            server_default="06:00:00",
        ),
    )


def downgrade() -> None:
    op.drop_column("ponds", "default_feed_time")
