"""User-owned strategy packages, public visibility, and explicit sharing.

Revision ID: f31c9aa26b41
Revises: e7c2a9b14d05
"""

import sqlalchemy as sa
from alembic import op

revision = "f31c9aa26b41"
down_revision = "e7c2a9b14d05"
branch_labels = None
depends_on = None


def _identity():
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade():
    identity = _identity()
    op.add_column("strategy_packages", sa.Column("owner_id", identity, nullable=True))
    op.add_column("strategy_packages", sa.Column(
        "visibility", sa.Text(), nullable=False, server_default="system",
    ))
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            "strategy_packages_owner_id_fkey", "strategy_packages", "users",
            ["owner_id"], ["id"],
        )
        op.create_check_constraint(
            "strategy_packages_visibility_check", "strategy_packages",
            "visibility in ('system','private','public')",
        )
    op.drop_index("strategy_packages_code_key_unique", table_name="strategy_packages")
    op.drop_index("strategy_packages_name_unique", table_name="strategy_packages")
    op.create_index(
        "strategy_packages_system_code_unique", "strategy_packages", ["code_key"],
        unique=True, sqlite_where=sa.text("owner_id IS NULL"),
        postgresql_where=sa.text("owner_id IS NULL"),
    )
    op.create_index(
        "strategy_packages_owner_code_unique", "strategy_packages", ["owner_id", "code_key"],
        unique=True, sqlite_where=sa.text("owner_id IS NOT NULL"),
        postgresql_where=sa.text("owner_id IS NOT NULL"),
    )
    op.create_table(
        "strategy_package_shares",
        sa.Column("package_id", identity, sa.ForeignKey("strategy_packages.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("user_id", identity, sa.ForeignKey("users.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )


def downgrade():
    op.drop_table("strategy_package_shares")
    op.drop_index("strategy_packages_owner_code_unique", table_name="strategy_packages")
    op.drop_index("strategy_packages_system_code_unique", table_name="strategy_packages")
    # Reverting requires every code_key and title to be globally unique again.
    op.create_index("strategy_packages_code_key_unique", "strategy_packages", ["code_key"], unique=True)
    op.create_index("strategy_packages_name_unique", "strategy_packages", [sa.text("lower(name)")], unique=True)
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("strategy_packages_visibility_check", "strategy_packages", type_="check")
        op.drop_constraint("strategy_packages_owner_id_fkey", "strategy_packages", type_="foreignkey")
    op.drop_column("strategy_packages", "visibility")
    op.drop_column("strategy_packages", "owner_id")
