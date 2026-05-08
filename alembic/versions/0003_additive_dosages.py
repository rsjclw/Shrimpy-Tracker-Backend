"""set additive dosage values

Revision ID: 0003_additive_dosages
Revises: 0002_additive_dosage
Create Date: 2026-05-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_additive_dosages"
down_revision: Union[str, None] = "0002_additive_dosage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOSAGES = {
    "Lactobacillus": 100,
    "AB": 1,
    "Bile Acid": 1,
    "Stimuno": 2,
    "Vitagold": 2,
    "Sodium Hummate": 2,
}


def upgrade() -> None:
    for name, dosage in DOSAGES.items():
        op.execute(
            sa.text("UPDATE feed_additives SET dosage_gr_per_kg = :d WHERE name = :n").bindparams(d=dosage, n=name)
        )


def downgrade() -> None:
    op.execute("UPDATE feed_additives SET dosage_gr_per_kg = NULL")
