"""daily mortality: dead shrimp found that day, tracked only

Revision ID: 0033_mortality
Revises: 0032_dose_per_kg
Create Date: 2026-10-04

One count per day on the day log, like its ABW sample. It never changes the
population model or anything computed from it: the survival rate at the end of
a cycle already captures losses. It is recorded and charted only.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033_mortality"
down_revision: Union[str, None] = "0032_dose_per_kg"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("daily_logs", sa.Column("mortality_count", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_daily_logs_mortality_count_not_negative",
        "daily_logs",
        "mortality_count IS NULL OR mortality_count >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_daily_logs_mortality_count_not_negative", "daily_logs", type_="check")
    op.drop_column("daily_logs", "mortality_count")
