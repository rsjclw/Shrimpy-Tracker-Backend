"""link feeding additives to the farm additive catalog

Feedings stored additives as {"name", "dosage_gr_per_kg"} with a whole-number
dose. They now also carry the catalog "additive_id" and allow decimal doses.
This backfills additive_id by matching names (ignoring case and extra spaces)
against the feeding's farm catalog; entries with no match keep additive_id null.

Revision ID: 0022_feeding_additive_ids
Revises: 0021_pond_feed_time
Create Date: 2026-09-22

"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022_feeding_additive_ids"
down_revision: Union[str, None] = "0021_pond_feed_time"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _norm(name: str) -> str:
    return " ".join(str(name).split()).lower()


def upgrade() -> None:
    conn = op.get_bind()
    catalog: dict[str, dict[str, int]] = {}
    for farm_id, additive_id, name in conn.execute(sa.text("SELECT farm_id, id, name FROM feed_additives")):
        catalog.setdefault(str(farm_id), {})[_norm(name)] = additive_id

    rows = conn.execute(
        sa.text(
            """
            SELECT f.id, f.additives::text, g.farm_id
            FROM feeding_sessions f
            JOIN daily_logs d ON d.id = f.daily_log_id
            JOIN cycles c ON c.id = d.cycle_id
            JOIN ponds p ON p.id = c.pond_id
            JOIN grids g ON g.id = p.grid_id
            WHERE f.additives::text <> '[]'
            """
        )
    )
    for feeding_id, additives_text, farm_id in rows.fetchall():
        entries = json.loads(additives_text or "[]")
        names = catalog.get(str(farm_id), {})
        for entry in entries:
            if entry.get("additive_id") is None:
                entry["additive_id"] = names.get(_norm(entry.get("name", "")))
        conn.execute(
            sa.text("UPDATE feeding_sessions SET additives = CAST(:a AS json) WHERE id = :id"),
            {"a": json.dumps(entries), "id": feeding_id},
        )


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, additives::text FROM feeding_sessions WHERE additives::text <> '[]'"))
    for feeding_id, additives_text in rows.fetchall():
        entries = json.loads(additives_text or "[]")
        for entry in entries:
            entry.pop("additive_id", None)
            # The old schema only accepted whole-number doses.
            if entry.get("dosage_gr_per_kg") is not None:
                entry["dosage_gr_per_kg"] = int(round(float(entry["dosage_gr_per_kg"])))
        conn.execute(
            sa.text("UPDATE feeding_sessions SET additives = CAST(:a AS json) WHERE id = :id"),
            {"a": json.dumps(entries), "id": feeding_id},
        )
