"""feeding session updated_at/updated_by/updated_by_type

Revision ID: 0019_feeding_updated_by
Revises: 0018_grid_location_env
Create Date: 2026-09-10

Keep revision ids under 32 characters - alembic_version.version_num is
varchar(32) and a longer id fails only at the very end of the migration.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019_feeding_updated_by"
down_revision: Union[str, None] = "0018_grid_location_env"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (name, type) pairs rather than Column instances - a Column cannot be reused
# across add_column calls, and Column.copy() is gone in SQLAlchemy 2.0.
FEEDING_COLUMNS = (
    ("updated_at", sa.DateTime(timezone=True)),
    ("updated_by", sa.String(100)),
    ("updated_by_type", sa.String(20)),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    existing = {column["name"] for column in inspector.get_columns("feeding_sessions")}
    for name, column_type in FEEDING_COLUMNS:
        if name not in existing:
            server_default = sa.func.now() if name == "updated_at" else None
            op.add_column(
                "feeding_sessions",
                sa.Column(name, column_type, server_default=server_default),
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    existing = {column["name"] for column in inspector.get_columns("feeding_sessions")}
    for name, _ in reversed(FEEDING_COLUMNS):
        if name in existing:
            op.drop_column("feeding_sessions", name)
