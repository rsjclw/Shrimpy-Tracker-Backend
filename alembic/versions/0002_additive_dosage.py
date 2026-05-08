"""additive dosage and replace seed data

Revision ID: 0002_additive_dosage
Revises: 0001_init
Create Date: 2026-05-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_additive_dosage"
down_revision: Union[str, None] = "0001_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ADDITIVES = [
    ("Lactobacillus", None),
    ("AB", None),
    ("Bile Acid", None),
    ("Stimuno", None),
    ("Vitagold", None),
    ("Sodium Hummate", None),
]


def upgrade() -> None:
    op.add_column(
        "feed_additives",
        sa.Column("dosage_gr_per_kg", sa.Numeric(8, 3), nullable=True),
    )
    # Clear old seed data and insert the correct additives
    op.execute("DELETE FROM feed_additives")
    op.execute(
        sa.text(
            "INSERT INTO feed_additives (name, dosage_gr_per_kg) VALUES "
            + ", ".join(
                f"('{name}', {dosage if dosage is not None else 'NULL'})"
                for name, dosage in ADDITIVES
            )
        )
    )


def downgrade() -> None:
    op.execute("DELETE FROM feed_additives")
    op.drop_column("feed_additives", "dosage_gr_per_kg")
