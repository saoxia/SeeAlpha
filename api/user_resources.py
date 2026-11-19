"""Persist user resource grants and overrides independently of running containers.

Missing rows preserve legacy access. Deployment-wide settings and runtime policy
remain authoritative; an allocation alone never provisions a production Engine.
"""
from __future__ import annotations

import json

from sqlalchemy import select

from .db.models import Tenant, UserResourceSetting

KINDS = ("harness", "development", "production")
DEFAULT = {
    kind: {"enabled": True, "cpus": None, "memory": None, "node": None}
    for kind in KINDS
}
# Development market-data access is an opt-in product capability, not an Engine limit.
DEFAULT["development"]["realtime_enabled"] = False


class UserResources:
    def __init__(self, database):
        self.database = database

    def get(self, user_id):
        if self.database.backend == "sqlite":
            with self.database.connect() as connection:
                row = connection.execute(
                    "SELECT configuration_json FROM user_resource_settings WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
                raw = row["configuration_json"] if row else None
        else:
            with self.database.session() as session:
                record = session.get(UserResourceSetting, user_id)
                raw = record.configuration_json if record else None
        data = json.loads(raw) if raw else {}
        return {kind: {**DEFAULT[kind], **data.get(kind, {})} for kind in KINDS}

    def save(self, user_id, settings):
        raw = json.dumps(settings, sort_keys=True, separators=(",", ":"))
        if self.database.backend == "sqlite":
            with self.database.connect() as connection:
                connection.execute(
                    """INSERT INTO user_resource_settings (user_id, configuration_json)
                       VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE
                       SET configuration_json = excluded.configuration_json""",
                    (user_id, raw),
                )
        else:
            with self.database.session(write=True) as session:
                session.merge(UserResourceSetting(user_id=user_id, configuration_json=raw))
        return self.get(user_id)

    def enabled(self, user_id, kind):
        return self.get(user_id)[kind]["enabled"]

    def for_tenant(self, tenant_id, purpose):
        if purpose not in {"production", "development"}:
            return DEFAULT["development"]
        if self.database.backend == "sqlite":
            with self.database.connect() as connection:
                row = connection.execute(
                    "SELECT owner_id FROM tenants WHERE id = ?", (tenant_id,),
                ).fetchone()
                owner_id = row["owner_id"] if row else None
        else:
            with self.database.session() as session:
                owner_id = session.scalar(
                    select(Tenant.owner_id).where(Tenant.id == tenant_id)
                )
        return self.get(owner_id)[purpose] if owner_id else DEFAULT[purpose]
