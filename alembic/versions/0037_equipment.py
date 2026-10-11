"""equipment registry (replaces places)

Revision ID: 0037_equipment
Revises: 0036_places
Create Date: 2026-10-11

One registry for everything on a grid that isn't a pond: reservoirs, bore wells,
paddlewheels, pumps, generators, PLN meters. Where each sits, what it moves water
or power to, and what happened to it are dated rows, so nothing is overwritten.
Claude and the farm agents fill it through the API.

places (0036) was deployed the same day and never held a row; it is dropped
outright. A reservoir's disinfections are equipment events.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037_equipment"
down_revision: Union[str, None] = "0036_places"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KINDS = "'reservoir', 'bore_well', 'paddlewheel', 'pump', 'generator', 'pln_meter'"
EVENT_KINDS = "'broken', 'sent_for_repair', 'repaired', 'serviced', 'part_replaced', 'disinfected', 'note', 'retired'"


def _uuid(name: str, target: str | None = None, nullable: bool = True) -> sa.Column:
    if target:
        return sa.Column(name, postgresql.UUID(as_uuid=True), sa.ForeignKey(f"{target}.id", ondelete="CASCADE"), nullable=nullable)
    return sa.Column(name, postgresql.UUID(as_uuid=True), primary_key=True)


def _created() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.drop_index("ix_places_grid_id", table_name="places")
    op.drop_table("places")

    op.create_table(
        "equipment",
        _uuid("id"),
        _uuid("grid_id", "grids", nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column("specs", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        _created(),
        sa.UniqueConstraint("grid_id", "code", name="uq_equipment_grid_code"),
        sa.CheckConstraint(f"kind IN ({KINDS})", name="ck_equipment_kind"),
    )
    op.create_index("ix_equipment_grid_id", "equipment", ["grid_id"])

    op.create_table(
        "equipment_placements",
        _uuid("id"),
        _uuid("equipment_id", "equipment", nullable=False),
        _uuid("pond_id", "ponds"),
        _uuid("in_equipment_id", "equipment"),
        _uuid("warehouse_id", "warehouses"),
        sa.Column("x_m", sa.Numeric(8, 2), nullable=True),
        sa.Column("y_m", sa.Numeric(8, 2), nullable=True),
        sa.Column("bearing", sa.Integer(), nullable=True),
        sa.Column("from_date", sa.Date(), nullable=False),
        sa.Column("to_date", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        _created(),
        sa.CheckConstraint("num_nonnulls(pond_id, in_equipment_id, warehouse_id) <= 1", name="ck_equipment_placements_one_place"),
        sa.CheckConstraint("bearing IS NULL OR (bearing >= 0 AND bearing <= 359)", name="ck_equipment_placements_bearing"),
        sa.CheckConstraint("to_date IS NULL OR to_date >= from_date", name="ck_equipment_placements_dates"),
    )
    op.create_index("ix_equipment_placements_equipment_id", "equipment_placements", ["equipment_id"])

    op.create_table(
        "equipment_links",
        _uuid("id"),
        _uuid("equipment_id", "equipment", nullable=False),
        _uuid("from_pond_id", "ponds"),
        _uuid("from_equipment_id", "equipment"),
        _uuid("to_pond_id", "ponds"),
        _uuid("to_equipment_id", "equipment"),
        sa.Column("from_date", sa.Date(), nullable=True),
        sa.Column("to_date", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        _created(),
        sa.CheckConstraint("num_nonnulls(from_pond_id, from_equipment_id) <= 1", name="ck_equipment_links_one_from"),
        sa.CheckConstraint("num_nonnulls(to_pond_id, to_equipment_id) = 1", name="ck_equipment_links_one_to"),
        sa.CheckConstraint("to_date IS NULL OR from_date IS NULL OR to_date >= from_date", name="ck_equipment_links_dates"),
    )
    op.create_index("ix_equipment_links_equipment_id", "equipment_links", ["equipment_id"])

    op.create_table(
        "equipment_events",
        _uuid("id"),
        _uuid("equipment_id", "equipment", nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("cost", sa.Numeric(14, 2), nullable=True),
        sa.Column("run_hours", sa.Numeric(10, 1), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        _created(),
        sa.CheckConstraint(f"kind IN ({EVENT_KINDS})", name="ck_equipment_events_kind"),
        sa.CheckConstraint("cost IS NULL OR cost >= 0", name="ck_equipment_events_cost"),
    )
    op.create_index("ix_equipment_events_equipment_id", "equipment_events", ["equipment_id"])


def downgrade() -> None:
    for table in ("equipment_events", "equipment_links", "equipment_placements"):
        op.drop_index(f"ix_{table}_equipment_id", table_name=table)
        op.drop_table(table)
    op.drop_index("ix_equipment_grid_id", table_name="equipment")
    op.drop_table("equipment")

    # Back to 0036: an empty places table.
    op.create_table(
        "places",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("grid_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("grids.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("width_m", sa.Numeric(8, 2), nullable=True),
        sa.Column("length_m", sa.Numeric(8, 2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("grid_id", "name", name="uq_places_grid_name"),
        sa.CheckConstraint("kind IN ('reservoir', 'bore_well')", name="ck_places_kind"),
    )
    op.create_index("ix_places_grid_id", "places", ["grid_id"])
