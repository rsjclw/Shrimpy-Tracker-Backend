"""equipment kind: blower

Revision ID: 0038_blower_kind
Revises: 0037_equipment
Create Date: 2026-10-11

Air blowers (ring and roots; the type goes in specs.type) join the equipment kinds.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0038_blower_kind"
down_revision: Union[str, None] = "0037_equipment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BEFORE = "'reservoir', 'bore_well', 'paddlewheel', 'pump', 'generator', 'pln_meter'"
AFTER = BEFORE + ", 'blower'"


def upgrade() -> None:
    op.drop_constraint("ck_equipment_kind", "equipment", type_="check")
    op.create_check_constraint("ck_equipment_kind", "equipment", f"kind IN ({AFTER})")


def downgrade() -> None:
    # Blowers can't exist under the old list; they go, with their history (cascade).
    op.execute("DELETE FROM equipment WHERE kind = 'blower'")
    op.drop_constraint("ck_equipment_kind", "equipment", type_="check")
    op.create_check_constraint("ck_equipment_kind", "equipment", f"kind IN ({BEFORE})")
