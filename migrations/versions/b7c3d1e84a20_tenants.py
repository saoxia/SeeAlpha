"""Add tenants, empty templates, and tenant scope for accounts and market intent.

Revision ID: b7c3d1e84a20
Revises: a9f42e716c08
"""

import time

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b7c3d1e84a20"
down_revision = "a9f42e716c08"
branch_labels = None
depends_on = None


def _identity():
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _document():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _advance_id_sequence(table):
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    schema = op.get_context().opts.get("version_table_schema")
    qualified = f"{schema}.{table}" if schema else table
    connection.execute(sa.text(
        "SELECT setval(pg_get_serial_sequence(:qualified, 'id'), "
        "GREATEST((SELECT COALESCE(MAX(id), 1) FROM "
        + qualified
        + "), 1), true)"
    ), {"qualified": qualified})


def upgrade():
    connection = op.get_bind()
    schema = op.get_context().opts.get("version_table_schema")
    inspector = sa.inspect(connection)
    identity = _identity()
    now = int(time.time())
    if not inspector.has_table("tenant_templates", schema=schema):
        op.create_table(
            "tenant_templates",
            sa.Column("id", identity, primary_key=True, autoincrement=True),
            sa.Column("purpose", sa.Text(), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("created_at", sa.BigInteger(), nullable=False),
            sa.CheckConstraint(
                "purpose in ('production','development')", name="tenant_templates_purpose_check"
            ),
        )
        op.create_index(
            "tenant_templates_name_unique",
            "tenant_templates",
            [sa.text("lower(name)")],
            unique=True,
        )
    templates = sa.table(
        "tenant_templates",
        sa.column("purpose", sa.Text()),
        sa.column("name", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("created_at", sa.BigInteger()),
    )
    existing_templates = {
        row.purpose
        for row in connection.execute(sa.select(templates.c.purpose, templates.c.name))
    }
    missing = [
        {"purpose": purpose, "name": name, "description": None, "created_at": now}
        for purpose, name in (("production", "空正式模板"), ("development", "空开发模板"))
        if purpose not in existing_templates
    ]
    if missing:
        op.bulk_insert(templates, missing)
    if not inspector.has_table("tenants", schema=schema):
        op.create_table(
            "tenants",
            sa.Column("id", identity, primary_key=True, autoincrement=True),
            sa.Column("template_id", identity, nullable=False),
            sa.Column("purpose", sa.Text(), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", sa.Text(), nullable=False, server_default="active"),
            sa.Column("system_key", sa.Text(), nullable=True),
            sa.Column("created_at", sa.BigInteger(), nullable=False),
            sa.Column("archived_at", sa.BigInteger(), nullable=True),
            sa.CheckConstraint("purpose in ('production','development')", name="tenants_purpose_check"),
            sa.CheckConstraint("status in ('active','archived')", name="tenants_status_check"),
        )
        op.create_index("tenants_name_unique", "tenants", [sa.text("lower(name)")], unique=True)
        op.create_index("tenants_system_key_unique", "tenants", ["system_key"], unique=True)
    template_rows = sa.table(
        "tenant_templates",
        sa.column("id", identity),
        sa.column("purpose", sa.Text()),
    )
    tenant_rows = sa.table(
        "tenants",
        sa.column("id", identity),
        sa.column("template_id", identity),
        sa.column("purpose", sa.Text()),
        sa.column("name", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("status", sa.Text()),
        sa.column("system_key", sa.Text()),
        sa.column("created_at", sa.BigInteger()),
        sa.column("archived_at", sa.BigInteger()),
    )
    production_template = connection.execute(
        sa.select(template_rows.c.id).where(template_rows.c.purpose == "production")
    ).scalar_one()
    default_tenant = connection.execute(
        sa.select(tenant_rows.c.id).where(tenant_rows.c.system_key == "default")
    ).scalar_one_or_none()
    if default_tenant is None:
        op.bulk_insert(tenant_rows, [{
            "id": 1,
            "template_id": production_template,
            "purpose": "production",
            "name": "默认正式",
            "description": None,
            "status": "active",
            "system_key": "default",
            "created_at": now,
            "archived_at": None,
        }])
        default_tenant = 1
    _advance_id_sequence("tenant_templates")
    _advance_id_sequence("tenants")
    if not inspector.has_table("virtual_accounts", schema=schema):
        op.create_table(
            "virtual_accounts",
            sa.Column("id", identity, primary_key=True, autoincrement=True),
            sa.Column("tenant_id", identity, nullable=False),
            sa.Column("account_name", sa.Text(), nullable=False),
            sa.Column("initial_capital", sa.Text(), nullable=False),
            sa.Column("quote_currency", sa.Text(), nullable=False),
            sa.Column("created_at", sa.BigInteger(), nullable=False),
            sa.Column("updated_at", sa.BigInteger(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="virtual_accounts_tenant_id_fkey"),
        )
        op.create_index(
            "virtual_accounts_name_unique",
            "virtual_accounts",
            ["tenant_id", sa.text("lower(account_name)")],
            unique=True,
        )
    account_columns = {column["name"] for column in inspector.get_columns("exchange_accounts", schema=schema)}
    if "tenant_id" not in account_columns:
        op.create_table(
            "exchange_accounts_next",
            sa.Column("id", identity, primary_key=True, autoincrement=True),
            sa.Column("tenant_id", identity, nullable=False),
            sa.Column("exchange", sa.Text(), nullable=False),
            sa.Column("account_name", sa.Text(), nullable=False),
            sa.Column("environment", sa.Text(), nullable=False),
            sa.Column("credentials_encrypted", sa.Text(), nullable=False),
            sa.Column("market_symbols_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("last_test_status", sa.Text(), nullable=True),
            sa.Column("last_tested_at", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.BigInteger(), nullable=False),
            sa.Column("updated_at", sa.BigInteger(), nullable=False),
            sa.CheckConstraint(
                "environment in ('production','sandbox')", name="exchange_accounts_environment_check"
            ),
            sa.CheckConstraint(
                "last_test_status is null or last_test_status in ('success','failed')",
                name="exchange_accounts_test_status_check",
            ),
            sa.ForeignKeyConstraint(
                ["tenant_id"], ["tenants.id"], name="exchange_accounts_tenant_id_fkey"
            ),
        )
        previous_accounts = sa.table(
            "exchange_accounts",
            sa.column("id", identity),
            sa.column("exchange", sa.Text()),
            sa.column("account_name", sa.Text()),
            sa.column("environment", sa.Text()),
            sa.column("credentials_encrypted", sa.Text()),
            sa.column("market_symbols_json", sa.Text()),
            sa.column("last_test_status", sa.Text()),
            sa.column("last_tested_at", sa.BigInteger()),
            sa.column("created_at", sa.BigInteger()),
            sa.column("updated_at", sa.BigInteger()),
        )
        copied_accounts = [
            {
                "id": row.id,
                "tenant_id": default_tenant,
                "exchange": row.exchange,
                "account_name": row.account_name,
                "environment": row.environment,
                "credentials_encrypted": row.credentials_encrypted,
                "market_symbols_json": row.market_symbols_json,
                "last_test_status": row.last_test_status,
                "last_tested_at": row.last_tested_at,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in connection.execute(sa.select(previous_accounts))
        ]
        if copied_accounts:
            op.bulk_insert(sa.table(
                "exchange_accounts_next",
                sa.column("id", identity),
                sa.column("tenant_id", identity),
                sa.column("exchange", sa.Text()),
                sa.column("account_name", sa.Text()),
                sa.column("environment", sa.Text()),
                sa.column("credentials_encrypted", sa.Text()),
                sa.column("market_symbols_json", sa.Text()),
                sa.column("last_test_status", sa.Text()),
                sa.column("last_tested_at", sa.BigInteger()),
                sa.column("created_at", sa.BigInteger()),
                sa.column("updated_at", sa.BigInteger()),
            ), copied_accounts)
        op.drop_table("exchange_accounts")
        op.rename_table("exchange_accounts_next", "exchange_accounts")
        _advance_id_sequence("exchange_accounts")
    else:
        foreign_keys = {
            key["name"] for key in sa.inspect(connection).get_foreign_keys("exchange_accounts", schema=schema)
        }
        if "exchange_accounts_tenant_id_fkey" not in foreign_keys:
            op.create_foreign_key(
                "exchange_accounts_tenant_id_fkey", "exchange_accounts", "tenants", ["tenant_id"], ["id"]
            )
    op.create_index(
        "orm_account_tenant_name_unique",
        "exchange_accounts",
        ["tenant_id", "exchange", sa.text("lower(account_name)")],
        unique=True,
        if_not_exists=True,
    )
    policy_columns = {
        column["name"] for column in inspector.get_columns("market_data_policies", schema=schema)
    }
    if "tenant_id" not in policy_columns:
        op.create_table(
            "market_data_policies_next",
            sa.Column("tenant_id", identity, nullable=False),
            sa.Column("product", sa.Text(), nullable=False),
            sa.Column("symbol", sa.Text(), nullable=False),
            sa.Column("parameters", _document(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="market_data_policies_tenant_id_fkey"),
            sa.PrimaryKeyConstraint("tenant_id", "product", "symbol"),
        )
        previous = sa.table(
            "market_data_policies",
            sa.column("product", sa.Text()),
            sa.column("symbol", sa.Text()),
            sa.column("parameters", _document()),
        )
        copied = [
            {
                "tenant_id": default_tenant,
                "product": row.product,
                "symbol": row.symbol,
                "parameters": row.parameters,
            }
            for row in connection.execute(sa.select(previous))
        ]
        if copied:
            op.bulk_insert(sa.table(
                "market_data_policies_next",
                sa.column("tenant_id", identity),
                sa.column("product", sa.Text()),
                sa.column("symbol", sa.Text()),
                sa.column("parameters", _document()),
            ), copied)
        op.drop_table("market_data_policies")
        op.rename_table("market_data_policies_next", "market_data_policies")


def downgrade():
    raise RuntimeError("租户结构不能自动删除；请从备份恢复。")
