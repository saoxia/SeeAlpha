"""Business database repository for tenant market-data policy intent."""

from sqlalchemy import select

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

