"""add cycle prediction config

Revision ID: 0014_cycle_prediction_config
Revises: 0013_harvest_total_price
Create Date: 2026-06-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0014_cycle_prediction_config"
down_revision: Union[str, None] = "0013_harvest_total_price"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cycles", sa.Column("prediction_config", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("cycles", "prediction_config")
