"""Database configuration and local operation records have separate ownership."""

import json
import os
from uuid import uuid4

from sqlalchemy import select

from api.db.errors import StorageUnavailable
from api.db.models import MarketDataPolicy


class PolicyRepository:
    def __init__(self, database):
        self.database = database

    def list(self, tenant_id=None):
        with self.database.session() as session:
            statement = select(MarketDataPolicy)
            if tenant_id is not None:
                statement = statement.where(MarketDataPolicy.tenant_id == tenant_id)
            return [dict(row.parameters) for row in session.scalars(statement).all()]

    def records(self, tenant_id):
        with self.database.session() as session:
            rows = session.scalars(
                select(MarketDataPolicy).where(MarketDataPolicy.tenant_id == tenant_id)
            ).all()
            return [
                {"tenant_id": row.tenant_id, "product": row.product, "symbol": row.symbol,
                 "parameters": dict(row.parameters)}
                for row in rows
            ]

    def save(self, policies, *, tenant_id):
        # The API's inherited ownership lease serializes local configuration changes.
        with self.database.session(write=True, lock_key=f"market-data:configuration:{tenant_id}") as session:
            existing = {
                (row.product, row.symbol): row
                for row in session.scalars(
                    select(MarketDataPolicy).where(MarketDataPolicy.tenant_id == tenant_id)
                ).all()
            }
            for policy in policies:
                key = (policy.product, policy.symbol)
                row = existing.pop(key, None)
                if row is None:
                    session.add(MarketDataPolicy(
                        tenant_id=tenant_id, product=policy.product, symbol=policy.symbol,
                        parameters=policy.model_dump(mode="json"),
                    ))
                else:
                    row.parameters = policy.model_dump(mode="json")
            for row in existing.values():
                session.delete(row)

    def upsert(self, tenant_id, policy):
        """Store one symbol's intent without replacing the tenant's other symbols."""
        with self.database.session(write=True, lock_key=f"market-data:configuration:{tenant_id}") as session:
            row = session.get(MarketDataPolicy, (tenant_id, policy.product, policy.symbol))
            if row is None:
                session.add(MarketDataPolicy(
                    tenant_id=tenant_id, product=policy.product, symbol=policy.symbol,
                    parameters=policy.model_dump(mode="json"),
                ))
            else:
                row.parameters = policy.model_dump(mode="json")
        return policy.model_dump(mode="json")


class OperationFiles:
    """Compatibility HTTP operations keep their status in files, never business ORM."""

    def __init__(self, directory):
        self.directory = directory

    def list_jobs(self):
        try:
            return [json.loads(path.read_text()) for path in self.directory.glob("*.json")]
        except (OSError, ValueError):
            raise StorageUnavailable("数据操作记录无法读取；原文件已保留。") from None

    def save_job(self, job):
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{job['id']}.json"
        temporary = target.with_suffix(f".{uuid4().hex}.tmp")
        try:
            with temporary.open("x") as stream:
                json.dump(job, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
