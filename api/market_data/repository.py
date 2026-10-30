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

    def list(self):
        with self.database.session() as session:
            return [dict(value) for value in session.scalars(select(MarketDataPolicy.parameters)).all()]

    def save(self, policies):
        # The API's inherited ownership lease serializes local configuration changes.
        with self.database.session(write=True, lock_key="market-data:configuration") as session:
            existing = {(row.product, row.symbol): row for row in session.scalars(select(MarketDataPolicy)).all()}
            for policy in policies:
                key = (policy.product, policy.symbol)
                row = existing.pop(key, None)
                if row is None:
                    session.add(MarketDataPolicy(product=policy.product, symbol=policy.symbol,
                                                 parameters=policy.model_dump(mode="json")))
                else:
                    row.parameters = policy.model_dump(mode="json")
            for row in existing.values():
                session.delete(row)


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
