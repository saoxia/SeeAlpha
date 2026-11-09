"""Add tenant ownership and impersonation to fresh business schemas.

Revision ID: e7c2a9b14d05
Revises: d8a41c6e2b17
"""


import sqlalchemy as sa
from alembic import op

revision = "e7c2a9b14d05"
down_revision = "d8a41c6e2b17"
branch_labels = None
depends_on = None


def _identity():
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade():
    identity = _identity()
    op.add_column("tenants", sa.Column("owner_id", identity, nullable=True))
    op.add_column("sessions", sa.Column("impersonate_user_id", identity, nullable=True))
    # SQLite cannot add a foreign key with ALTER TABLE. PostgreSQL can.
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key("tenants_owner_id_fkey", "tenants", "users", ["owner_id"], ["id"])
        op.create_foreign_key(
            "sessions_impersonate_user_id_fkey",
            "sessions",
            "users",
            ["impersonate_user_id"],
            ["id"],
        )
    op.create_index("tenants_owner_idx", "tenants", ["owner_id"])


def downgrade():
    op.drop_index("tenants_owner_idx", table_name="tenants")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("sessions_impersonate_user_id_fkey", "sessions", type_="foreignkey")
        op.drop_constraint("tenants_owner_id_fkey", "tenants", type_="foreignkey")
    op.drop_column("sessions", "impersonate_user_id")
    op.drop_column("tenants", "owner_id")
