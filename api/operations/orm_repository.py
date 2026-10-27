"""Transactional ORM repository for deduplicated trading-operation evidence."""

import time
import uuid

from sqlalchemy import delete, func, or_, select, update

from api.db.models import LedgerAlias, LedgerObservation, LedgerOperation, LedgerSync

from .models import Observation
from .repository import observation_key, same_order


class ORMOperations:
    def __init__(self, database):
        self.database = database

    def initialize(self):
        pass  # Schema is managed by Alembic.

    def ingest(self, observation):
        record = Observation.model_validate(observation).model_dump()
        key = observation_key(record)
        # A scope lock prevents concurrent events for the same instrument from
        # creating conflicting identities, including when no rows yet exist.
        lock_key = "ledger:" + ":".join(
            str(record[k]) for k in ("account_id", "environment", "exchange", "symbol")
        )
        with self.database.session(write=True, lock_key=lock_key) as session:
            previous = session.get(LedgerObservation, key)
            if previous:
                if previous.body != record:
                    raise ValueError("相同事件标识包含冲突的内容，请使用新的事件标识保留修订。")
                return {
                    "operation_id": previous.operation_id,
                    "created": False,
                    "observation_added": False,
                }
            scope = select(LedgerOperation).where(
                LedgerOperation.account_id == record["account_id"],
                LedgerOperation.environment == record["environment"],
                LedgerOperation.exchange == record["exchange"],
                LedgerOperation.symbol == record["symbol"],
            )
            candidates = [dict(item.body) for item in session.scalars(scope).all()]
            linked = [item for item in candidates if same_order(item, record)]
            if record.get("strategy_id") and any(
                item.get("strategy_id") and item["strategy_id"] != record["strategy_id"]
                for item in linked
            ):
                raise ValueError("同一订单的策略归属存在冲突。")
            matches = [
                item
                for item in candidates
                if item["event_type"] == record["event_type"]
                and (
                    (record["event_type"] == "fill" and item["trade_id"] == record["trade_id"])
                    or (
                        record["event_type"] != "fill"
                        and same_order(item, record)
                        and (
                            record["event_type"] not in ("amend", "correction")
                            or item["timestamp"] == record["timestamp"]
                        )
                    )
                )
            ]
            matches.sort(
                key=lambda item: (item["source"] != "project", item["timestamp"], item["id"])
            )
            old = matches[0] if matches else None
            if old:
                for duplicate in matches[1:]:
                    if duplicate["confirmed"] and not old["confirmed"]:
                        for field in (
                            "amount",
                            "price",
                            "cost",
                            "filled",
                            "fee_cost",
                            "fee_currency",
                            "status",
                            "confirmed",
                        ):
                            old[field] = duplicate.get(field)
                    session.execute(
                        update(LedgerObservation)
                        .where(LedgerObservation.operation_id == duplicate["id"])
                        .values(operation_id=old["id"])
                    )
                    session.execute(
                        update(LedgerAlias)
                        .where(LedgerAlias.operation_id == duplicate["id"])
                        .values(operation_id=old["id"])
                    )
                    alias = session.get(LedgerAlias, duplicate["id"])
                    if alias:
                        alias.operation_id = old["id"]
                    else:
                        session.add(LedgerAlias(id=duplicate["id"], operation_id=old["id"]))
                    session.flush()
                    session.execute(
                        delete(LedgerOperation).where(LedgerOperation.id == duplicate["id"])
                    )
            strategy = record.get("strategy_id") or next(
                (item.get("strategy_id") for item in linked if item["source"] == "project"), None
            )
            project = record["evidence"] == "project" or any(
                item["source"] == "project" for item in linked
            )
            source = (
                "reconciliation"
                if record["evidence"] == "reconciliation"
                else ("project" if project else "external")
            )
            body = {
                **(old or record),
                **{k: v for k, v in record.items() if v is not None and v != ""},
                "id": old["id"] if old else str(uuid.uuid4()),
                "source": source,
                "strategy_id": strategy,
                "confirmed": bool(record["evidence"] == "venue" or (old or {}).get("confirmed")),
            }
            if old:
                body["timestamp"] = old["timestamp"]
                if old["confirmed"] and record["evidence"] != "venue":
                    for field in (
                        "amount",
                        "price",
                        "cost",
                        "filled",
                        "fee_cost",
                        "fee_currency",
                        "status",
                    ):
                        body[field] = old.get(field)
            self._save(session, body)
            session.flush()
            session.add(LedgerObservation(id=key, operation_id=body["id"], body=record))
            if project:
                duplicate_ids = {item["id"] for item in matches[1:]}
                for item in linked:
                    if (
                        item["id"] == body["id"]
                        or item["id"] in duplicate_ids
                        or item["source"] == "reconciliation"
                    ):
                        continue
                    item.update(source="project", strategy_id=strategy or item.get("strategy_id"))
                    self._save(session, item)
            return {"operation_id": body["id"], "created": old is None, "observation_added": True}

    @staticmethod
    def _save(session, body):
        names = (
            "id",
            "account_id",
            "environment",
            "exchange",
            "symbol",
            "event_type",
            "timestamp",
            "client_order_id",
            "order_id",
            "trade_id",
            "source",
            "strategy_id",
        )
        item = session.get(LedgerOperation, body["id"])
        if not item:
            item = LedgerOperation(id=body["id"])
            session.add(item)
        for name in names[1:]:
            setattr(item, name, body.get(name))
        item.body = dict(body)

    def list(self, *, cursor=None, limit=50, **filters):
        query = select(LedgerOperation).order_by(
            LedgerOperation.timestamp.desc(), LedgerOperation.id.desc()
        )
        for key, value in filters.items():
            if value is None or value == "":
                continue
            if key in ("account_id", "environment", "strategy_id", "source", "event_type"):
                query = query.where(getattr(LedgerOperation, key) == value)
            elif key == "symbol":
                query = query.where(
                    func.lower(LedgerOperation.symbol).contains(value.lower(), autoescape=True)
                )
            elif key == "start_ms":
                query = query.where(LedgerOperation.timestamp >= value)
            elif key == "end_ms":
                query = query.where(LedgerOperation.timestamp <= value)
        if cursor:
            query = query.where(
                or_(
                    LedgerOperation.timestamp < cursor[0],
                    (LedgerOperation.timestamp == cursor[0]) & (LedgerOperation.id < cursor[1]),
                )
            )
        with self.database.session() as session:
            rows = session.scalars(query.limit(limit + 1)).all()
            return {
                "records": [dict(item.body) for item in rows[:limit]],
                "has_more": len(rows) > limit,
            }

    def detail(self, operation_id):
        with self.database.session() as session:
            alias = session.get(LedgerAlias, operation_id)
            item = session.get(LedgerOperation, alias.operation_id if alias else operation_id)
            if item is None:
                return None
            observations = session.scalars(
                select(LedgerObservation.body)
                .where(LedgerObservation.operation_id == item.id)
                .order_by(LedgerObservation.id)
            ).all()
            return {"operation": dict(item.body), "observations": list(observations)}

    def sync_state(self, account_id=None):
        query = select(LedgerSync.body)
        if account_id:
            query = query.where(LedgerSync.account_id == account_id)
        with self.database.session() as session:
            return list(session.scalars(query).all())

    def save_sync(self, **state):
        state["checked_at"] = int(time.time() * 1000)
        key = f"sync:{state['account_id']}:{state['environment']}"
        with self.database.session(write=True, lock_key=key) as session:
            item = session.get(LedgerSync, (state["account_id"], state["environment"]))
            if item:
                item.body = state
            else:
                session.add(
                    LedgerSync(
                        account_id=state["account_id"], environment=state["environment"], body=state
                    )
                )
