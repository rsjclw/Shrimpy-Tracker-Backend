"""grid coordinates and cached daily environment

Revision ID: 0018_grid_location_and_environment
Revises: 0017_water_clarity
Create Date: 2026-07-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_grid_location_and_environment"
down_revision: Union[str, None] = "0017_water_clarity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (name, type) pairs rather than Column instances - a Column cannot be reused
# across add_column calls, and Column.copy() is gone in SQLAlchemy 2.0.
GRID_COLUMNS = (
    ("latitude", sa.Numeric(9, 6)),
    ("longitude", sa.Numeric(9, 6)),
    ("timezone", sa.String(64)),
    ("elevation_m", sa.Numeric(7, 2)),
    ("weather_synced_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    existing = {column["name"] for column in inspector.get_columns("grids")}
    for name, column_type in GRID_COLUMNS:
        if name not in existing:
            op.add_column("grids", sa.Column(name, column_type))

    if "daily_environment" not in inspector.get_table_names():
        op.create_table(
            "daily_environment",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "grid_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("grids.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("date", sa.Date, nullable=False),
            sa.Column("temp_min_c", sa.Numeric(5, 2)),
            sa.Column("temp_max_c", sa.Numeric(5, 2)),
            sa.Column("temp_mean_c", sa.Numeric(5, 2)),
            sa.Column("shortwave_radiation_sum_mj", sa.Numeric(7, 2)),
            sa.Column("sunshine_duration_hours", sa.Numeric(5, 2)),
            sa.Column("cloud_cover_daylight_pct", sa.Numeric(5, 2)),
            sa.Column("precipitation_mm", sa.Numeric(7, 2)),
            sa.Column("precipitation_hours", sa.Numeric(5, 2)),
            sa.Column("precipitation_probability_max_pct", sa.Numeric(5, 2)),
            sa.Column("is_forecast", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("source", sa.String(32), nullable=False, server_default="open-meteo"),
            sa.Column("fetched_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("grid_id", "date", name="uq_daily_environment_grid_date"),
        )
        op.create_index("ix_daily_environment_grid_id", "daily_environment", ["grid_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if "daily_environment" in inspector.get_table_names():
        op.drop_index("ix_daily_environment_grid_id", table_name="daily_environment")
        op.drop_table("daily_environment")

    existing = {column["name"] for column in inspector.get_columns("grids")}
    for name, _ in reversed(GRID_COLUMNS):
        if name in existing:
            op.drop_column("grids", name)
