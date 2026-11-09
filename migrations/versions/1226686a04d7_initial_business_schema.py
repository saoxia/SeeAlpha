"""initial business schema

Revision ID: 1226686a04d7
Revises:
Create Date: 2026-10-27 14:13:47.501284

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "1226686a04d7"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    connection = op.get_bind()
    schema = op.get_context().opts.get("version_table_schema")
    tables = set(sa.inspect(connection).get_table_names(schema=schema))
    if tables - {"alembic_version"}:
        raise RuntimeError("目标业务 schema 已存在旧表；请使用全新、空的数据库/schema 初始化。")
    # New, empty databases receive the initial schema.
    op.create_table(
        "exchange_accounts",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("exchange", sa.Text(), nullable=False),
        sa.Column("account_name", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("credentials_encrypted", sa.Text(), nullable=False),
        sa.Column("market_symbols_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("last_test_status", sa.Text(), nullable=True),
        sa.Column("last_tested_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("environment in ('production','sandbox')"),
        sa.CheckConstraint("last_test_status is null or last_test_status in ('success','failed')"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "orm_account_name_unique",
        "exchange_accounts",
        ["exchange", sa.text("lower(account_name)")],
        unique=True,
    )
    op.create_table(
        "ledger_operations",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column(
            "account_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("exchange", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.BigInteger(), nullable=False),
        sa.Column("client_order_id", sa.Text(), nullable=True),
        sa.Column("order_id", sa.Text(), nullable=True),
        sa.Column("trade_id", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("strategy_id", sa.Text(), nullable=True),
        sa.Column(
            "body",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "orm_ledger_account_time",
        "ledger_operations",
        [
            "account_id",
            "environment",
            sa.literal_column("timestamp DESC"),
            sa.literal_column("id DESC"),
        ],
        unique=False,
    )
    op.create_index(
        "orm_ledger_client",
        "ledger_operations",
        ["account_id", "environment", "exchange", "symbol", "client_order_id"],
        unique=False,
    )
    op.create_index(
        "orm_ledger_fill",
        "ledger_operations",
        ["account_id", "environment", "exchange", "symbol", "trade_id"],
        unique=True,
        sqlite_where=sa.text("event_type = 'fill'"),
        postgresql_where=sa.text("event_type = 'fill'"),
    )
    op.create_index(
        "orm_ledger_order",
        "ledger_operations",
        ["account_id", "environment", "exchange", "symbol", "order_id"],
        unique=False,
    )
    op.create_index(
        "orm_ledger_time",
        "ledger_operations",
        [sa.literal_column("timestamp DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_table(
        "ledger_sync",
        sa.Column(
            "account_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column(
            "body",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("account_id", "environment"),
    )
    op.create_table(
        "login_failures",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("identity_hash", sa.Text(), nullable=False),
        sa.Column("attempted_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "orm_login_failures_lookup",
        "login_failures",
        ["identity_hash", "attempted_at"],
        unique=False,
    )
    op.create_table(
        "olap_state",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "value",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.CheckConstraint("name in ('settings', 'monitor', 'cache')"),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "users",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("orm_users_username_unique", "users", [sa.text("lower(username)")], unique=True)
    op.create_table(
        "ledger_aliases",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("operation_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["ledger_operations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ledger_aliases_operation_id"), "ledger_aliases", ["operation_id"], unique=False
    )
    op.create_table(
        "ledger_observations",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("operation_id", sa.Text(), nullable=False),
        sa.Column(
            "body",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["ledger_operations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ledger_observations_operation_id"),
        "ledger_observations",
        ["operation_id"],
        unique=False,
    )
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("user_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index(op.f("ix_sessions_expires_at"), "sessions", ["expires_at"], unique=False)
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    """Initial schema is not automatically dropped to protect business data."""
    raise RuntimeError("初始业务库不能自动删除，请重建测试数据库或从备份恢复。")
