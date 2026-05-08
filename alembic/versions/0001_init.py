"""initial schema

Revision ID: 0001_init
Revises:
Create Date: 2026-05-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_init"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "grids",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True)),
    )

    op.create_table(
        "ponds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "grid_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grids.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("area_m2", sa.Numeric(10, 2)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ponds_grid_id", "ponds", ["grid_id"])

    op.create_table(
        "cycles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "pond_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ponds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("planned_end_date", sa.Date()),
        sa.Column("actual_end_date", sa.Date()),
        sa.Column("initial_population", sa.Integer(), nullable=False),
        sa.Column("initial_abw_g", sa.Numeric(10, 4), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("notes", sa.Text()),
    )
    op.create_index("ix_cycles_pond_id", "cycles", ["pond_id"])

    op.create_table(
        "daily_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "cycle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cycles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("abw_g", sa.Numeric(10, 4)),
        sa.Column("notes", sa.Text()),
        sa.UniqueConstraint("cycle_id", "date", name="uq_daily_logs_cycle_date"),
    )
    op.create_index("ix_daily_logs_cycle_date", "daily_logs", ["cycle_id", "date"])

    op.create_table(
        "feeding_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "daily_log_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("daily_logs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("feed_time", sa.Time(), nullable=False),
        sa.Column("amount_kg", sa.Numeric(10, 3), nullable=False),
        sa.Column("duration_min", sa.Integer()),
        sa.Column("additives", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("notes", sa.Text()),
    )
    op.create_index("ix_feeding_sessions_daily_log_id", "feeding_sessions", ["daily_log_id"])

    op.create_table(
        "water_parameters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "daily_log_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("daily_logs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("do_am", sa.Numeric(6, 2)),
        sa.Column("do_pm", sa.Numeric(6, 2)),
        sa.Column("ph_am", sa.Numeric(4, 2)),
        sa.Column("ph_pm", sa.Numeric(4, 2)),
        sa.Column("salinity", sa.Numeric(6, 2)),
        sa.Column("tan", sa.Numeric(6, 3)),
        sa.Column("nitrite", sa.Numeric(6, 3)),
        sa.Column("phosphate", sa.Numeric(6, 3)),
        sa.Column("calcium", sa.Numeric(8, 2)),
        sa.Column("magnesium", sa.Numeric(8, 2)),
        sa.Column("alkalinity", sa.Numeric(8, 2)),
    )

    op.create_table(
        "treatments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "daily_log_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("daily_logs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("treatment_time", sa.Time(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("worker", sa.String(100)),
        sa.Column("notes", sa.Text()),
    )
    op.create_index("ix_treatments_daily_log_id", "treatments", ["daily_log_id"])

    op.create_table(
        "population_samples",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "cycle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cycles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("population", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(50)),
        sa.Column("notes", sa.Text()),
    )
    op.create_index("ix_population_samples_cycle_date", "population_samples", ["cycle_id", "date"])

    op.create_table(
        "feed_additives",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
    )


def downgrade() -> None:
    op.drop_table("feed_additives")
    op.drop_index("ix_population_samples_cycle_date", table_name="population_samples")
    op.drop_table("population_samples")
    op.drop_index("ix_treatments_daily_log_id", table_name="treatments")
    op.drop_table("treatments")
    op.drop_table("water_parameters")
    op.drop_index("ix_feeding_sessions_daily_log_id", table_name="feeding_sessions")
    op.drop_table("feeding_sessions")
    op.drop_index("ix_daily_logs_cycle_date", table_name="daily_logs")
    op.drop_table("daily_logs")
    op.drop_index("ix_cycles_pond_id", table_name="cycles")
    op.drop_table("cycles")
    op.drop_index("ix_ponds_grid_id", table_name="ponds")
    op.drop_table("ponds")
    op.drop_table("grids")
