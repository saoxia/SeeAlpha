"""Tenant, template, and virtual-account persistence for SQLite and ORM databases."""

import sqlite3
import time

from sqlalchemy import delete, func, select

from api.db.errors import StorageConflict
from api.db.models import (
    EquityEvent,
    EquityPoint,
    EquityRebuild,
    EquityState,
    ExchangeAccount,
    LedgerAlias,
    LedgerObservation,
    LedgerOperation,
    LedgerSync,
    MarketDataPolicy,
    Tenant,
    TenantTemplate,
    VirtualAccount,
)


def _mapping(row, *fields):
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return {field: row[field] for field in fields}
    return {field: getattr(row, field) for field in fields}


_TEMPLATE = ("id", "purpose", "name", "description", "created_at")
_TENANT = (
    "id", "template_id", "purpose", "name", "description", "status", "system_key",
    "created_at", "archived_at",
)
_VIRTUAL = (
    "id", "tenant_id", "account_name", "initial_capital", "quote_currency", "created_at", "updated_at",
)


class TenantRepository:
    def __init__(self, database):
        self.database = database

    def list_templates(self):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                rows = connection.execute(
                    "SELECT id, purpose, name, description, created_at FROM tenant_templates ORDER BY id"
                ).fetchall()
            return [_mapping(row, *_TEMPLATE) for row in rows]
        with self.database.session() as session:
            rows = session.scalars(select(TenantTemplate).order_by(TenantTemplate.id)).all()
            return [_mapping(row, *_TEMPLATE) for row in rows]

    def create_template(self, purpose, name, description):
        now = int(time.time())
        try:
            if self.database.backend != "orm":
                with self.database.connect() as connection:
                    cursor = connection.execute(
                        """INSERT INTO tenant_templates (purpose, name, description, created_at)
                           VALUES (?, ?, ?, ?)""",
                        (purpose, name, description, now),
                    )
                    row = connection.execute(
                        "SELECT id, purpose, name, description, created_at FROM tenant_templates WHERE id = ?",
                        (cursor.lastrowid,),
                    ).fetchone()
                return _mapping(row, *_TEMPLATE)
            with self.database.session(write=True) as session:
                template = TenantTemplate(
                    purpose=purpose, name=name, description=description, created_at=now
                )
                session.add(template)
                session.flush()
                return _mapping(template, *_TEMPLATE)
        except sqlite3.IntegrityError:
            raise StorageConflict("模板名称不能重复。") from None

    def get_template(self, template_id):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                row = connection.execute(
                    "SELECT id, purpose, name, description, created_at FROM tenant_templates WHERE id = ?",
                    (template_id,),
                ).fetchone()
            return _mapping(row, *_TEMPLATE)
        with self.database.session() as session:
            return _mapping(session.get(TenantTemplate, template_id), *_TEMPLATE)

    def list_tenants(self):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                rows = connection.execute(
                    """SELECT id, template_id, purpose, name, description, status, system_key,
                              created_at, archived_at
                       FROM tenants ORDER BY id"""
                ).fetchall()
            return [_mapping(row, *_TENANT) for row in rows]
        with self.database.session() as session:
            rows = session.scalars(select(Tenant).order_by(Tenant.id)).all()
            return [_mapping(row, *_TENANT) for row in rows]

    def get_tenant(self, tenant_id):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                row = connection.execute(
                    """SELECT id, template_id, purpose, name, description, status, system_key,
                              created_at, archived_at
                       FROM tenants WHERE id = ?""",
                    (tenant_id,),
                ).fetchone()
            return _mapping(row, *_TENANT)
        with self.database.session() as session:
            return _mapping(session.get(Tenant, tenant_id), *_TENANT)

    def require_default(self):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                row = connection.execute(
                    """SELECT id, template_id, purpose, name, description, status, system_key,
                              created_at, archived_at
                       FROM tenants WHERE system_key = 'default'"""
                ).fetchone()
            return _mapping(row, *_TENANT)
        with self.database.session() as session:
            tenant = session.scalar(select(Tenant).where(Tenant.system_key == "default"))
            return _mapping(tenant, *_TENANT)

    def create_tenant(self, template_id, purpose, name, description):
        now = int(time.time())
        try:
            if self.database.backend != "orm":
                with self.database.connect() as connection:
                    cursor = connection.execute(
                        """INSERT INTO tenants (
                               template_id, purpose, name, description, status, created_at
                           ) VALUES (?, ?, ?, ?, 'active', ?)""",
                        (template_id, purpose, name, description, now),
                    )
                    row = connection.execute(
                        """SELECT id, template_id, purpose, name, description, status, system_key,
                                  created_at, archived_at
                           FROM tenants WHERE id = ?""",
                        (cursor.lastrowid,),
                    ).fetchone()
                return _mapping(row, *_TENANT)
            with self.database.session(write=True) as session:
                tenant = Tenant(
                    template_id=template_id, purpose=purpose, name=name, description=description,
                    status="active", created_at=now,
                )
                session.add(tenant)
                session.flush()
                return _mapping(tenant, *_TENANT)
        except sqlite3.IntegrityError:
            raise StorageConflict("租户名称不能重复。") from None

    def set_status(self, tenant_id, status):
        archived_at = int(time.time()) if status == "archived" else None
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                connection.execute(
                    "UPDATE tenants SET status = ?, archived_at = ? WHERE id = ?",
                    (status, archived_at, tenant_id),
                )
            return self.get_tenant(tenant_id)
        with self.database.session(write=True) as session:
            tenant = session.get(Tenant, tenant_id)
            tenant.status = status
            tenant.archived_at = archived_at
            session.flush()
            return _mapping(tenant, *_TENANT)

    def list_virtual_accounts(self, tenant_id):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                rows = connection.execute(
                    """SELECT id, tenant_id, account_name, initial_capital, quote_currency,
                              created_at, updated_at
                       FROM virtual_accounts WHERE tenant_id = ? ORDER BY id""",
                    (tenant_id,),
                ).fetchall()
            return [_mapping(row, *_VIRTUAL) for row in rows]
        with self.database.session() as session:
            rows = session.scalars(
                select(VirtualAccount).where(VirtualAccount.tenant_id == tenant_id).order_by(VirtualAccount.id)
            ).all()
            return [_mapping(row, *_VIRTUAL) for row in rows]

    def create_virtual_account(self, tenant_id, account_name, initial_capital, quote_currency):
        now = int(time.time())
        try:
            if self.database.backend != "orm":
                with self.database.connect() as connection:
                    cursor = connection.execute(
                        """INSERT INTO virtual_accounts (
                               tenant_id, account_name, initial_capital, quote_currency, created_at, updated_at
                           ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (tenant_id, account_name, initial_capital, quote_currency, now, now),
                    )
                    row = connection.execute(
                        """SELECT id, tenant_id, account_name, initial_capital, quote_currency,
                                  created_at, updated_at
                           FROM virtual_accounts WHERE id = ?""",
                        (cursor.lastrowid,),
                    ).fetchone()
                return _mapping(row, *_VIRTUAL)
            with self.database.session(write=True) as session:
                account = VirtualAccount(
                    tenant_id=tenant_id, account_name=account_name, initial_capital=initial_capital,
                    quote_currency=quote_currency, created_at=now, updated_at=now,
                )
                session.add(account)
                session.flush()
                return _mapping(account, *_VIRTUAL)
        except sqlite3.IntegrityError:
            raise StorageConflict("同一租户内虚拟账户名称不能重复。") from None

    def exchange_account_ids(self, tenant_id):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                return [
                    row[0]
                    for row in connection.execute(
                        "SELECT id FROM exchange_accounts WHERE tenant_id = ?", (tenant_id,)
                    )
                ]
        with self.database.session() as session:
            return list(session.scalars(
                select(ExchangeAccount.id).where(ExchangeAccount.tenant_id == tenant_id)
            ).all())

    def delete_records(self, tenant_id):
        if self.database.backend != "orm":
            self._delete_sqlite(tenant_id)
            return
        account_ids = self.exchange_account_ids(tenant_id)
        with self.database.session(write=True) as session:
            if account_ids:
                operation_ids = list(session.scalars(
                    select(LedgerOperation.id).where(LedgerOperation.account_id.in_(account_ids))
                ).all())
                if operation_ids:
                    session.execute(delete(LedgerObservation).where(
                        LedgerObservation.operation_id.in_(operation_ids)
                    ))
                    session.execute(delete(LedgerAlias).where(LedgerAlias.operation_id.in_(operation_ids)))
                    session.execute(delete(LedgerOperation).where(LedgerOperation.id.in_(operation_ids)))
                session.execute(delete(LedgerSync).where(LedgerSync.account_id.in_(account_ids)))
                event_ids = list(session.scalars(
                    select(EquityEvent.id).where(EquityEvent.account_id.in_(account_ids))
                ).all())
                if event_ids:
                    session.execute(delete(EquityPoint).where(EquityPoint.event_id.in_(event_ids)))
                    session.execute(delete(EquityEvent).where(EquityEvent.id.in_(event_ids)))
                session.execute(delete(EquityState).where(EquityState.account_id.in_(account_ids)))
                session.execute(delete(EquityRebuild).where(EquityRebuild.account_id.in_(account_ids)))
            session.execute(delete(MarketDataPolicy).where(MarketDataPolicy.tenant_id == tenant_id))
            session.execute(delete(VirtualAccount).where(VirtualAccount.tenant_id == tenant_id))
            session.execute(delete(ExchangeAccount).where(ExchangeAccount.tenant_id == tenant_id))
            session.execute(delete(Tenant).where(Tenant.id == tenant_id))

    def _delete_sqlite(self, tenant_id):
        with self.database.connect() as connection:
            names = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            account_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM exchange_accounts WHERE tenant_id = ?", (tenant_id,)
                )
            ]
            if account_ids and "ledger_operations" in names:
                marks = ",".join("?" for _ in account_ids)
                operation_ids = [
                    row[0]
                    for row in connection.execute(
                        f"SELECT id FROM ledger_operations WHERE account_id IN ({marks})", account_ids
                    )
                ]
                if operation_ids:
                    op_marks = ",".join("?" for _ in operation_ids)
                    connection.execute(
                        f"DELETE FROM ledger_observations WHERE operation_id IN ({op_marks})", operation_ids
                    )
                    connection.execute(
                        f"DELETE FROM ledger_aliases WHERE operation_id IN ({op_marks})", operation_ids
                    )
                    connection.execute(
                        f"DELETE FROM ledger_operations WHERE id IN ({op_marks})", operation_ids
                    )
                connection.execute(
                    f"DELETE FROM ledger_sync WHERE account_id IN ({marks})", account_ids
                )
            connection.execute("DELETE FROM virtual_accounts WHERE tenant_id = ?", (tenant_id,))
            connection.execute("DELETE FROM exchange_accounts WHERE tenant_id = ?", (tenant_id,))
            connection.execute("DELETE FROM tenants WHERE id = ?", (tenant_id,))

    def related_records(self, tenant_id):
        """Account-keyed ledger and equity rows that belong to this tenant."""
        if self.database.backend != "orm":
            return {}
        account_ids = self.exchange_account_ids(tenant_id)
        if not account_ids:
            return {
                "ledger_operations": [], "ledger_observations": [], "ledger_aliases": [],
                "ledger_sync": [], "equity_events": [], "equity_points": [],
                "equity_state": [], "equity_rebuilds": [],
            }
        with self.database.session() as session:
            operations = session.scalars(
                select(LedgerOperation).where(LedgerOperation.account_id.in_(account_ids))
            ).all()
            operation_ids = [row.id for row in operations]
            observations = [] if not operation_ids else session.scalars(
                select(LedgerObservation).where(LedgerObservation.operation_id.in_(operation_ids))
            ).all()
            aliases = [] if not operation_ids else session.scalars(
                select(LedgerAlias).where(LedgerAlias.operation_id.in_(operation_ids))
            ).all()
            sync_rows = session.scalars(
                select(LedgerSync).where(LedgerSync.account_id.in_(account_ids))
            ).all()
            events = session.scalars(
                select(EquityEvent).where(EquityEvent.account_id.in_(account_ids))
            ).all()
            event_ids = [row.id for row in events]
            points = [] if not event_ids else session.scalars(
                select(EquityPoint).where(EquityPoint.event_id.in_(event_ids))
            ).all()
            states = session.scalars(
                select(EquityState).where(EquityState.account_id.in_(account_ids))
            ).all()
            rebuilds = session.scalars(
                select(EquityRebuild).where(EquityRebuild.account_id.in_(account_ids))
            ).all()

            def columns(row):
                return {column.name: getattr(row, column.name) for column in row.__table__.columns}

            return {
                "ledger_operations": [columns(row) for row in operations],
                "ledger_observations": [columns(row) for row in observations],
                "ledger_aliases": [columns(row) for row in aliases],
                "ledger_sync": [columns(row) for row in sync_rows],
                "equity_events": [columns(row) for row in events],
                "equity_points": [columns(row) for row in points],
                "equity_state": [columns(row) for row in states],
                "equity_rebuilds": [columns(row) for row in rebuilds],
            }

    def count_children(self, tenant_id):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                virtual = connection.execute(
                    "SELECT count(*) FROM virtual_accounts WHERE tenant_id = ?", (tenant_id,)
                ).fetchone()[0]
                exchange = connection.execute(
                    "SELECT count(*) FROM exchange_accounts WHERE tenant_id = ?", (tenant_id,)
                ).fetchone()[0]
            return {"virtual_accounts": virtual, "exchange_accounts": exchange, "policies": 0}
        with self.database.session() as session:
            return {
                "virtual_accounts": session.scalar(
                    select(func.count()).select_from(VirtualAccount).where(VirtualAccount.tenant_id == tenant_id)
                ),
                "exchange_accounts": session.scalar(
                    select(func.count()).select_from(ExchangeAccount).where(ExchangeAccount.tenant_id == tenant_id)
                ),
                "policies": session.scalar(
                    select(func.count()).select_from(MarketDataPolicy).where(
                        MarketDataPolicy.tenant_id == tenant_id
                    )
                ),
            }
