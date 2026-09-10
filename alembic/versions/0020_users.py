"""local users table (replaces Supabase Auth)

Revision ID: 0020_users
Revises: 0019_feeding_updated_by
Create Date: 2026-09-10

Keep revision ids under 32 characters - alembic_version.version_num is
varchar(32) and a longer id fails only at the very end of the migration.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_users"
down_revision: Union[str, None] = "0019_feeding_updated_by"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if "users" not in inspector.get_table_names():
        op.create_table(
            "users",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("email", sa.String(255), nullable=False),
            sa.Column("password_hash", sa.String(255), nullable=False),
            sa.Column("must_change_password", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("last_login_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("email", name="uq_users_email"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if "users" in inspector.get_table_names():
        op.drop_table("users")
