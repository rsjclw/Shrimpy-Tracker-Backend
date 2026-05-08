"""add abw sample time

Revision ID: 0007_abw_sample_time
Revises: 0006_cycle_feeding_fields
Create Date: 2026-05-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_abw_sample_time"
down_revision: Union[str, None] = "0006_cycle_feeding_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("daily_logs", sa.Column("abw_sample_time", sa.Time(), nullable=True))


def downgrade() -> None:
    op.drop_column("daily_logs", "abw_sample_time")
