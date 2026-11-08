"""Strategy package, version, grant, and issuance records."""

import json
import sqlite3
import time

from sqlalchemy import delete, select

from api.db.errors import StorageConflict
from api.db.models import StrategyGrant, StrategyIssuance, StrategyPackage, StrategyPackageVersion


def _mapping(row, *fields):
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return {field: row[field] for field in fields}
    return {field: getattr(row, field) for field in fields}


def _parameters(value):
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


_PACKAGE = ("id", "code_key", "name", "description", "created_at")
_VERSION = (
    "id", "package_id", "version", "parameters", "entry_point", "package_hash", "archive", "created_at",
)
_GRANT = ("id", "version_id", "purpose", "created_at")
_ISSUANCE = ("id", "tenant_id", "account_id", "version_id", "purpose", "parameters", "created_at")


class StrategyRepository:
    def __init__(self, database):
        self.database = database

    def list_packages(self):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                rows = connection.execute(
                    """SELECT id, code_key, name, description, created_at
                       FROM strategy_packages ORDER BY id"""
                ).fetchall()
            return [_mapping(row, *_PACKAGE) for row in rows]
        with self.database.session() as session:
            rows = session.scalars(select(StrategyPackage).order_by(StrategyPackage.id)).all()
            return [_mapping(row, *_PACKAGE) for row in rows]

    def get_package(self, package_id):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                row = connection.execute(
                    """SELECT id, code_key, name, description, created_at
                       FROM strategy_packages WHERE id = ?""",
                    (package_id,),
                ).fetchone()
            return _mapping(row, *_PACKAGE)
        with self.database.session() as session:
            return _mapping(session.get(StrategyPackage, package_id), *_PACKAGE)

    def create_package(self, code_key, name, description):
        now = int(time.time())
        try:
            if self.database.backend != "orm":
                with self.database.connect() as connection:
                    cursor = connection.execute(
                        """INSERT INTO strategy_packages (code_key, name, description, created_at)
                           VALUES (?, ?, ?, ?)""",
                        (code_key, name, description, now),
                    )
                    row = connection.execute(
                        """SELECT id, code_key, name, description, created_at
                           FROM strategy_packages WHERE id = ?""",
                        (cursor.lastrowid,),
                    ).fetchone()
                return _mapping(row, *_PACKAGE)
            with self.database.session(write=True) as session:
                package = StrategyPackage(
                    code_key=code_key, name=name, description=description, created_at=now,
                )
                session.add(package)
                session.flush()
                return _mapping(package, *_PACKAGE)
        except sqlite3.IntegrityError:
            raise StorageConflict("策略包不能重复。") from None

    def list_versions(self, package_id):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                rows = connection.execute(
                    """SELECT id, package_id, version, parameters, entry_point, package_hash,
                              archive, created_at
                       FROM strategy_package_versions WHERE package_id = ? ORDER BY id""",
                    (package_id,),
                ).fetchall()
            return [_version(_mapping(row, *_VERSION)) for row in rows]
        with self.database.session() as session:
            rows = session.scalars(
                select(StrategyPackageVersion)
                .where(StrategyPackageVersion.package_id == package_id)
                .order_by(StrategyPackageVersion.id)
            ).all()
            return [_version(_mapping(row, *_VERSION)) for row in rows]

    def get_version(self, version_id):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                row = connection.execute(
                    """SELECT id, package_id, version, parameters, entry_point, package_hash,
                              archive, created_at
                       FROM strategy_package_versions WHERE id = ?""",
                    (version_id,),
                ).fetchone()
            mapped = _mapping(row, *_VERSION)
        else:
            with self.database.session() as session:
                mapped = _mapping(session.get(StrategyPackageVersion, version_id), *_VERSION)
        return _version(mapped) if mapped else None

    def create_version(self, package_id, version, parameters, entry_point, package_hash, archive):
        now = int(time.time())
        encoded = json.dumps(parameters, ensure_ascii=False, sort_keys=True)
        try:
            if self.database.backend != "orm":
                with self.database.connect() as connection:
                    cursor = connection.execute(
                        """INSERT INTO strategy_package_versions
                               (package_id, version, parameters, entry_point, package_hash,
                                archive, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (package_id, version, encoded, entry_point, package_hash, archive, now),
                    )
                    row = connection.execute(
                        """SELECT id, package_id, version, parameters, entry_point, package_hash,
                                  archive, created_at
                           FROM strategy_package_versions WHERE id = ?""",
                        (cursor.lastrowid,),
                    ).fetchone()
                return _version(_mapping(row, *_VERSION))
            with self.database.session(write=True) as session:
                stored = StrategyPackageVersion(
                    package_id=package_id, version=version, parameters=parameters,
                    entry_point=entry_point, package_hash=package_hash, archive=archive,
                    created_at=now,
                )
                session.add(stored)
                session.flush()
                return _version(_mapping(stored, *_VERSION))
        except sqlite3.IntegrityError:
            raise StorageConflict("策略版本不能重复。") from None

    def list_grants(self, version_id):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                rows = connection.execute(
                    """SELECT id, version_id, purpose, created_at
                       FROM strategy_grants WHERE version_id = ? ORDER BY id""",
                    (version_id,),
                ).fetchall()
            return [_mapping(row, *_GRANT) for row in rows]
        with self.database.session() as session:
            rows = session.scalars(
                select(StrategyGrant).where(StrategyGrant.version_id == version_id).order_by(StrategyGrant.id)
            ).all()
            return [_mapping(row, *_GRANT) for row in rows]

    def find_grant(self, version_id, purpose):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                row = connection.execute(
                    """SELECT id, version_id, purpose, created_at
                       FROM strategy_grants WHERE version_id = ? AND purpose = ?""",
                    (version_id, purpose),
                ).fetchone()
            return _mapping(row, *_GRANT)
        with self.database.session() as session:
            grant = session.scalar(
                select(StrategyGrant).where(
                    StrategyGrant.version_id == version_id, StrategyGrant.purpose == purpose,
                )
            )
            return _mapping(grant, *_GRANT)

    def create_grant(self, version_id, purpose):
        existing = self.find_grant(version_id, purpose)
        if existing is not None:
            return existing
        now = int(time.time())
        try:
            if self.database.backend != "orm":
                with self.database.connect() as connection:
                    cursor = connection.execute(
                        """INSERT INTO strategy_grants (version_id, purpose, created_at)
                           VALUES (?, ?, ?)""",
                        (version_id, purpose, now),
                    )
                    row = connection.execute(
                        """SELECT id, version_id, purpose, created_at FROM strategy_grants WHERE id = ?""",
                        (cursor.lastrowid,),
                    ).fetchone()
                return _mapping(row, *_GRANT)
            with self.database.session(write=True) as session:
                grant = StrategyGrant(version_id=version_id, purpose=purpose, created_at=now)
                session.add(grant)
                session.flush()
                return _mapping(grant, *_GRANT)
        except sqlite3.IntegrityError:
            raise StorageConflict("策略授权不能重复。") from None

    def list_issuances(self, tenant_id):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                rows = connection.execute(
                    """SELECT id, tenant_id, account_id, version_id, purpose, parameters, created_at
                       FROM strategy_issuances WHERE tenant_id = ? ORDER BY id""",
                    (tenant_id,),
                ).fetchall()
            return [_issuance(_mapping(row, *_ISSUANCE)) for row in rows]
        with self.database.session() as session:
            rows = session.scalars(
                select(StrategyIssuance)
                .where(StrategyIssuance.tenant_id == tenant_id)
                .order_by(StrategyIssuance.id)
            ).all()
            return [_issuance(_mapping(row, *_ISSUANCE)) for row in rows]

    def create_issuance(self, tenant_id, account_id, version_id, purpose, parameters):
        now = int(time.time())
        encoded = json.dumps(parameters, ensure_ascii=False, sort_keys=True)
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                cursor = connection.execute(
                    """INSERT INTO strategy_issuances
                           (tenant_id, account_id, version_id, purpose, parameters, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (tenant_id, account_id, version_id, purpose, encoded, now),
                )
                row = connection.execute(
                    """SELECT id, tenant_id, account_id, version_id, purpose, parameters, created_at
                       FROM strategy_issuances WHERE id = ?""",
                    (cursor.lastrowid,),
                ).fetchone()
            return _issuance(_mapping(row, *_ISSUANCE))
        with self.database.session(write=True) as session:
            issuance = StrategyIssuance(
                tenant_id=tenant_id, account_id=account_id, version_id=version_id,
                purpose=purpose, parameters=parameters, created_at=now,
            )
            session.add(issuance)
            session.flush()
            return _issuance(_mapping(issuance, *_ISSUANCE))

    def delete_issuance(self, issuance_id):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                connection.execute("DELETE FROM strategy_issuances WHERE id = ?", (issuance_id,))
            return
        with self.database.session(write=True) as session:
            session.execute(delete(StrategyIssuance).where(StrategyIssuance.id == issuance_id))


def _version(row):
    if row is None:
        return None
    archive = row["archive"]
    if isinstance(archive, memoryview):
        archive = bytes(archive)
    return {**row, "parameters": _parameters(row["parameters"]), "archive": archive}


def _issuance(row):
    return {**row, "parameters": _parameters(row["parameters"]), "account_id": str(row["account_id"])}
