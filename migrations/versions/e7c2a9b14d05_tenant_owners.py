"""Assign each tenant to a user and keep one production tenant per user.

Revision ID: e7c2a9b14d05
Revises: d8a41c6e2b17

The default production tenant (system_key 'default', name 默认正式) belongs to the
earliest user. Other users receive their own production tenant. Extra production
tenants are folded into that earliest user's production tenant: exchange account
rows move, they are not copied, and the extra tenant becomes a development tenant.
Virtual-account capital stays on the tenant it already belonged to.
"""

import time

import sqlalchemy as sa
from alembic import op

revision = "e7c2a9b14d05"
down_revision = "d8a41c6e2b17"
branch_labels = None
depends_on = None


def _identity():
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _production_template(connection):
    return connection.execute(sa.text(
        "SELECT id FROM tenant_templates WHERE purpose = 'production' ORDER BY id LIMIT 1"
    )).scalar()


def _has_production(connection, user_id):
    return connection.execute(sa.text(
        """SELECT 1 FROM tenants
           WHERE owner_id = :owner AND purpose = 'production' LIMIT 1"""
    ), {"owner": user_id}).first() is not None


def _available_name(connection, username, user_id):
    base = f"正式·{username}"
    if len(base) > 100:
        base = base[:100]
    taken = connection.execute(sa.text(
        "SELECT 1 FROM tenants WHERE lower(name) = lower(:name) LIMIT 1"
    ), {"name": base}).first()
    if taken is None:
        return base
    fallback = f"正式·{user_id}"
    return fallback[:100]


def _move_exchange_accounts(connection, source_id, target_id):
    """Move credential rows onto the owner's production tenant. Do not duplicate them."""
    accounts = connection.execute(sa.text(
        "SELECT id, exchange, account_name FROM exchange_accounts WHERE tenant_id = :source"
    ), {"source": source_id}).mappings().all()
    existing = {
        (row["exchange"], str(row["account_name"]).casefold())
        for row in connection.execute(sa.text(
            "SELECT exchange, account_name FROM exchange_accounts WHERE tenant_id = :target"
        ), {"target": target_id}).mappings()
    }
    for row in accounts:
        name = row["account_name"]
        if (row["exchange"], str(name).casefold()) in existing:
            renamed = f"{name}·迁入"
            if (row["exchange"], renamed.casefold()) in existing:
                renamed = f"{renamed}{row['id']}"
            name = renamed
        connection.execute(sa.text(
            """UPDATE exchange_accounts
               SET tenant_id = :target, account_name = :name
               WHERE id = :account_id"""
        ), {"target": target_id, "name": name, "account_id": row["id"]})
        existing.add((row["exchange"], str(name).casefold()))


def _assign_existing_owners(connection):
    users = connection.execute(sa.text(
        "SELECT id, username FROM users ORDER BY id"
    )).mappings().all()
    if not users:
        return
    primary_id = users[0]["id"]
    primary_tenant = connection.execute(sa.text(
        """SELECT id FROM tenants
           WHERE system_key = 'default' AND purpose = 'production'
           ORDER BY id LIMIT 1"""
    )).scalar()
    if primary_tenant is None:
        primary_tenant = connection.execute(sa.text(
            "SELECT id FROM tenants WHERE purpose = 'production' ORDER BY id LIMIT 1"
        )).scalar()
    if primary_tenant is not None:
        connection.execute(sa.text(
            """UPDATE tenants SET owner_id = :owner
               WHERE id = :tenant AND owner_id IS NULL"""
        ), {"owner": primary_id, "tenant": primary_tenant})
        extras = connection.execute(sa.text(
            """SELECT id FROM tenants
               WHERE purpose = 'production' AND id != :primary AND owner_id IS NULL
               ORDER BY id"""
        ), {"primary": primary_tenant}).scalars().all()
        for extra_id in extras:
            _move_exchange_accounts(connection, extra_id, primary_tenant)
            connection.execute(sa.text(
                """UPDATE tenants
                   SET purpose = 'development', owner_id = :owner
                   WHERE id = :tenant"""
            ), {"owner": primary_id, "tenant": extra_id})
    template_id = _production_template(connection)
    now = int(time.time())
    for user in users:
        if _has_production(connection, user["id"]) or template_id is None:
            continue
        connection.execute(sa.text(
            """INSERT INTO tenants (
                   template_id, owner_id, purpose, name, description, status, created_at
               ) VALUES (
                   :template_id, :owner_id, 'production', :name, NULL, 'active', :created_at
               )"""
        ), {
            "template_id": template_id,
            "owner_id": user["id"],
            "name": _available_name(connection, user["username"], user["id"]),
            "created_at": now,
        })
    connection.execute(sa.text(
        "UPDATE tenants SET owner_id = :owner WHERE owner_id IS NULL"
    ), {"owner": primary_id})


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
    _assign_existing_owners(op.get_bind())
    op.create_index("tenants_owner_idx", "tenants", ["owner_id"])
    op.create_index(
        "tenants_one_production_per_owner",
        "tenants",
        ["owner_id"],
        unique=True,
        sqlite_where=sa.text("purpose = 'production'"),
        postgresql_where=sa.text("purpose = 'production'"),
    )


def downgrade():
    op.drop_index("tenants_one_production_per_owner", table_name="tenants")
    op.drop_index("tenants_owner_idx", table_name="tenants")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("sessions_impersonate_user_id_fkey", "sessions", type_="foreignkey")
        op.drop_constraint("tenants_owner_id_fkey", "tenants", type_="foreignkey")
    op.drop_column("sessions", "impersonate_user_id")
    op.drop_column("tenants", "owner_id")
