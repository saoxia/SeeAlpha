"""Strategy package, version, grant, and issuance records."""

import json
import sqlite3
import time

from sqlalchemy import delete, select

from api.db.errors import StorageConflict
from api.db.models import (
    StrategyGrant,
    StrategyIssuance,
    StrategyPackage,
    StrategyPackageShare,
    StrategyPackageVersion,
)


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


_PACKAGE = ("id", "code_key", "name", "description", "owner_id", "visibility", "created_at")
_VERSION = (
    "id", "package_id", "version", "parameters", "entry_point", "package_hash", "archive", "created_at",
)
_GRANT = ("id", "version_id", "purpose", "created_at")
_ISSUANCE = ("id", "tenant_id", "account_id", "version_id", "purpose", "parameters", "created_at")


class StrategyRepository:
    def __init__(self, database):
        self.database = database

    def list_packages(self, viewer_id=None, is_admin=False):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                rows = connection.execute(
                    """SELECT id, code_key, name, description, owner_id, visibility, created_at
                       FROM strategy_packages ORDER BY id"""
                ).fetchall()
            packages = [_mapping(row, *_PACKAGE) for row in rows]
        else:
            with self.database.session() as session:
                rows = session.scalars(select(StrategyPackage).order_by(StrategyPackage.id)).all()
                packages = [_mapping(row, *_PACKAGE) for row in rows]
        if is_admin:
            return packages
        shared = self.shared_package_ids(viewer_id) if viewer_id is not None else set()
        return [
            item for item in packages
            if item["visibility"] in {"system", "public"}
            or (viewer_id is not None and item["owner_id"] == viewer_id)
            or item["id"] in shared
        ]

    def shared_package_ids(self, user_id):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                return {row["package_id"] for row in connection.execute(
                    "SELECT package_id FROM strategy_package_shares WHERE user_id = ?", (user_id,),
                )}
        with self.database.session() as session:
            return set(session.scalars(
                select(StrategyPackageShare.package_id)
                .where(StrategyPackageShare.user_id == user_id)
            ).all())

    def get_package(self, package_id):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                row = connection.execute(
                    """SELECT id, code_key, name, description, owner_id, visibility, created_at
                       FROM strategy_packages WHERE id = ?""",
                    (package_id,),
                ).fetchone()
            return _mapping(row, *_PACKAGE)
        with self.database.session() as session:
            return _mapping(session.get(StrategyPackage, package_id), *_PACKAGE)

    def create_package(self, code_key, name, description, owner_id=None, visibility="system"):
        if visibility not in {"system", "private", "public"} or (
            (owner_id is None) != (visibility == "system")
        ):
            raise ValueError("策略归属或可见性无效。")
        now = int(time.time())
        try:
            if self.database.backend != "orm":
                with self.database.connect() as connection:
                    cursor = connection.execute(
                        """INSERT INTO strategy_packages (code_key, name, description, owner_id, visibility, created_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (code_key, name, description, owner_id, visibility, now),
                    )
                    row = connection.execute(
                        """SELECT id, code_key, name, description, owner_id, visibility, created_at
                           FROM strategy_packages WHERE id = ?""",
                        (cursor.lastrowid,),
                    ).fetchone()
                return _mapping(row, *_PACKAGE)
            with self.database.session(write=True) as session:
                package = StrategyPackage(
                    code_key=code_key, name=name, description=description,
                    owner_id=owner_id, visibility=visibility, created_at=now,
                )
                session.add(package)
                session.flush()
                return _mapping(package, *_PACKAGE)
        except sqlite3.IntegrityError:
            raise StorageConflict("策略包不能重复。") from None

    def set_visibility(self, package_id, visibility):
        if visibility not in {"private", "public"}:
            raise ValueError("用户策略仅允许私有或公开。")
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                connection.execute(
                    "UPDATE strategy_packages SET visibility = ? WHERE id = ? AND owner_id IS NOT NULL",
                    (visibility, package_id),
                )
        else:
            with self.database.session(write=True) as session:
                package = session.get(StrategyPackage, package_id)
                if package is not None and package.owner_id is not None:
                    package.visibility = visibility

    def list_shares(self, package_id):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                return [int(row["user_id"]) for row in connection.execute(
                    "SELECT user_id FROM strategy_package_shares WHERE package_id = ? ORDER BY user_id",
                    (package_id,),
                )]
        with self.database.session() as session:
            return list(session.scalars(
                select(StrategyPackageShare.user_id)
                .where(StrategyPackageShare.package_id == package_id)
                .order_by(StrategyPackageShare.user_id)
            ).all())

    def add_share(self, package_id, user_id):
        if user_id in self.list_shares(package_id):
            return
        now = int(time.time())
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                connection.execute(
                    "INSERT INTO strategy_package_shares (package_id, user_id, created_at) VALUES (?, ?, ?)",
                    (package_id, user_id, now),
                )
        else:
            with self.database.session(write=True) as session:
                session.add(StrategyPackageShare(package_id=package_id, user_id=user_id, created_at=now))

    def remove_share(self, package_id, user_id):
        if self.database.backend != "orm":
            with self.database.connect() as connection:
                connection.execute(
                    "DELETE FROM strategy_package_shares WHERE package_id = ? AND user_id = ?",
                    (package_id, user_id),
                )
        else:
            with self.database.session(write=True) as session:
                session.execute(delete(StrategyPackageShare).where(
                    StrategyPackageShare.package_id == package_id,
                    StrategyPackageShare.user_id == user_id,
                ))

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
