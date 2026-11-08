"""Atomic valuation evidence/projection writes and replay under the account lock."""

import time
import uuid

from fastapi import HTTPException
from sqlalchemy import select, tuple_

from ..db.models import EquityEvent, EquityPoint, EquityRebuild, EquityState
from .valuation import value


class EquityRepository:
    def __init__(self, database):
        self.database = database

    def require(self):
        if self.database.backend != "orm":
            raise HTTPException(503, "资产曲线持久化需要已迁移的 ORM 业务数据库。")

    def lock(self, account_id, environment):
        return f"equity:{account_id}:{environment}"

    def state(self, account_id, environment):
        self.require()
        with self.database.session() as session:
            row = session.get(EquityState, (account_id, environment))
            return dict(row.body) if row else {"status": "pending", "message": "等待首次账户对账。"}

    def append(self, account_id, environment, source_id, evidence):
        self.require()
        projection = value(evidence)
        with self.database.session(write=True, lock_key=self.lock(account_id, environment)) as session:
            old = session.scalar(select(EquityEvent).where(EquityEvent.source_id == source_id))
            if old:
                if old.account_id != account_id or old.environment != environment or old.body != evidence:
                    raise ValueError("资产事件身份冲突，拒绝覆盖证据。")
                return old.id
            event = EquityEvent(account_id=account_id, environment=environment, source_id=source_id,
                                timestamp=evidence["time"], body=evidence)
            session.add(event)
            session.flush()
            session.add(EquityPoint(event_id=event.id, body=projection))
            state = session.get(EquityState, (account_id, environment))
            if state is None:
                state = EquityState(account_id=account_id, environment=environment, body={})
                session.add(state)
            previous = state.body
            state.body = {**previous, "status": "gap" if projection["gap"] else "ready",
                          "message": projection["error"], "last_event_at": evidence["time"],
                          "last_event_id": event.id, "latest": projection,
                          "started_at": previous.get("started_at", evidence["time"])}
            if evidence["reason"] in {"check", "calibration"}:
                state.body = {**state.body, "last_check_at": evidence["time"],
                              "reconciliation": evidence.get("reconciliation")}
            return event.id

    def points(self, accounts, start, end):
        """Stream one ordered cursor for all accounts; memory stays bounded for daily charts."""
        self.require()
        with self.database.session() as session:
            rows = session.execute(select(EquityEvent.timestamp, EquityEvent.id, EquityEvent.account_id,
                                          EquityPoint.body)
                                   .join(EquityPoint, EquityEvent.id == EquityPoint.event_id)
                                   .where(tuple_(EquityEvent.account_id, EquityEvent.environment).in_(accounts),
                                          EquityEvent.timestamp >= start, EquityEvent.timestamp <= end)
                                   .order_by(EquityEvent.timestamp, EquityEvent.id)
                                   .execution_options(yield_per=500))
            for timestamp, event_id, account_id, body in rows:
                yield timestamp, event_id, {**body, "account_id": account_id}

    def create_rebuild(self, account_id, environment, start, end):
        self.require()
        now = int(time.time() * 1000)
        with self.database.session(write=True, lock_key=self.lock(account_id, environment)) as session:
            jobs = session.scalars(select(EquityRebuild).where(EquityRebuild.account_id == account_id,
                                                             EquityRebuild.environment == environment))
            if any(job.body["status"] in {"queued", "running"} for job in jobs):
                raise HTTPException(409, "该账户已有资产重算任务。")
            job = EquityRebuild(id=uuid.uuid4().hex, account_id=account_id, environment=environment,
                                body={"status": "queued", "start_ms": start, "end_ms": end,
                                      "created_at": now, "processed": 0})
            session.add(job)
            return {"id": job.id, "account_id": account_id, "environment": environment, **job.body}

    def job(self, job_id):
        self.require()
        with self.database.session() as session:
            job = session.get(EquityRebuild, job_id)
            if job is None:
                raise HTTPException(404, "资产重算任务不存在。")
            return {"id": job.id, "account_id": job.account_id, "environment": job.environment, **job.body}

    def recover(self):
        self.require()
        with self.database.session(write=True) as session:
            for job in session.scalars(select(EquityRebuild)):
                if job.body["status"] in {"queued", "running"}:
                    job.body = {**job.body, "status": "interrupted", "message": "服务重启，重算已中断；可重新发起。"}

    def rebuild(self, job_id):
        job = self.job(job_id)
        with self.database.session(write=True, lock_key=self.lock(job["account_id"], job["environment"])) as session:
            row = session.get(EquityRebuild, job_id)
            row.body = {**row.body, "status": "running"}
        try:
            # One transaction serializes account appends and replaces only derived
            # points. Errors roll back every changed point, leaving raw evidence intact.
            with self.database.session(write=True, lock_key=self.lock(job["account_id"], job["environment"])) as session:
                row = session.get(EquityRebuild, job_id)
                query = select(EquityEvent).where(EquityEvent.account_id == job["account_id"],
                                                 EquityEvent.environment == job["environment"],
                                                 EquityEvent.timestamp <= job["end_ms"])
                if job["start_ms"] is not None:
                    query = query.where(EquityEvent.timestamp >= job["start_ms"])
                count, gaps = 0, 0
                for event in session.scalars(query.order_by(EquityEvent.id).execution_options(yield_per=500)):
                    point = value(event.body)
                    session.merge(EquityPoint(event_id=event.id, body=point))
                    count += 1
                    gaps += int(point["gap"])
                state = session.get(EquityState, (job["account_id"], job["environment"]))
                if state is not None:
                    latest = session.get(EquityPoint, state.body["last_event_id"])
                    state.body = {**state.body, "latest": dict(latest.body),
                                  "status": "gap" if latest.body["gap"] else "ready",
                                  "message": latest.body["error"]}
                row.body = {**row.body, "status": "success", "processed": count, "gaps": gaps,
                            "finished_at": int(time.time() * 1000),
                            "message": "已按保存的证据重算；历史缺口未补造。"}
        except Exception:
            with self.database.session(write=True, lock_key=self.lock(job["account_id"], job["environment"])) as session:
                row = session.get(EquityRebuild, job_id)
                row.body = {**row.body, "status": "failed", "message": "重算失败，原派生曲线和原始证据保留。"}
            raise
