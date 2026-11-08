"""Catalog strategy packages, versions, tenant-type grants, and issuances.

Revision ID: c4e8a1b27d09
Revises: b7c3d1e84a20
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c4e8a1b27d09"
down_revision = "b7c3d1e84a20"
branch_labels = None
depends_on = None


def _identity():
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _document():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    identity = _identity()
    document = _document()
    op.create_table(
        "strategy_packages",
        sa.Column("id", identity, primary_key=True, autoincrement=True),
        sa.Column("code_key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("strategy_packages_code_key_unique", "strategy_packages", ["code_key"], unique=True)
    op.create_index(
        "strategy_packages_name_unique",
        "strategy_packages",
        [sa.text("lower(name)")],
        unique=True,
    )
    op.create_table(
        "strategy_package_versions",
        sa.Column("id", identity, primary_key=True, autoincrement=True),
        sa.Column("package_id", identity, nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("parameters", document, nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["package_id"], ["strategy_packages.id"], name="strategy_package_versions_package_id_fkey",
        ),
        sa.UniqueConstraint("package_id", "version", name="strategy_package_versions_unique"),
    )
    op.create_index(
        "strategy_package_versions_package_idx", "strategy_package_versions", ["package_id"],
    )
    op.create_table(
        "strategy_grants",
        sa.Column("id", identity, primary_key=True, autoincrement=True),
        sa.Column("version_id", identity, nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "purpose in ('production','development')", name="strategy_grants_purpose_check",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["strategy_package_versions.id"], name="strategy_grants_version_id_fkey",
        ),
        sa.UniqueConstraint("version_id", "purpose", name="strategy_grants_version_purpose_unique"),
    )
    op.create_table(
        "strategy_issuances",
        sa.Column("id", identity, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", identity, nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("version_id", identity, nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("parameters", document, nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "purpose in ('production','development')", name="strategy_issuances_purpose_check",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="strategy_issuances_tenant_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["strategy_package_versions.id"], name="strategy_issuances_version_id_fkey",
        ),
    )
    op.create_index("strategy_issuances_tenant_idx", "strategy_issuances", ["tenant_id", "id"])
