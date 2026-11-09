"""Business market-data policy and Engine HTTP orchestration.

The Platform never reads Catalog, raw archives, feed journals, or data locks.
Engine owns the market-data filesystem and validates all previews/deletions.
"""

import json
import os
import threading
from uuid import uuid4

from fastapi import HTTPException

from core.paths import PROJECT_ROOT

from .repository import PolicyRepository


class DataService:
    def __init__(self, database, *, project=PROJECT_ROOT):
        self.project = project
        self.policy_repository = PolicyRepository(database) if database.backend == "orm" else None
        self.lock = threading.RLock()
        from .subscriptions import Subscriptions
        self.subscriptions = Subscriptions(self)
        from .monitor import ServiceMonitor
        self.monitor = ServiceMonitor(self)

    def reconcile_jobs(self):
        """Apply Engine-terminal policy changes to the business database once."""
        engine = self._required_engine()
        from .contracts import DataPolicy
        for job in engine.market_control("jobs"):
            if job["status"] != "succeeded" or job["action"] not in {"delete_pair", "trim"}:
                continue
            marker = self.project / ".local/state/api/market_settlements" / f"{job['id']}.json"
            if marker.exists():
                continue
            with self.lock:
                if marker.exists():
                    continue
                parameters = job["parameters"]
                if job["action"] == "delete_pair":
                    self.subscriptions.remove(parameters["symbol"], parameters["product"])
                else:
                    policy = DataPolicy.model_validate(parameters["policy"])
                    self.subscriptions._save(policy, allow_later_start=True)
                marker.parent.mkdir(parents=True, exist_ok=True)
                temporary = marker.with_suffix(f".{uuid4().hex}.tmp")
                try:
                    temporary.write_text(json.dumps({"job_id": job["id"], "applied": True}))
                    os.replace(temporary, marker)
                finally:
                    temporary.unlink(missing_ok=True)

    def storage(self):
        return self._required_engine().market_control("storage")

    def require_storage(self, *, write=False):
        state = self.storage()
        if not state["available"] or write and not state["writable"]:
            raise HTTPException(503, state["message"] or "数据存储不可写")

    def plan(self, payload):
        return self._required_engine().data_query(
            "prepare-plan", payload.model_dump(mode="json"),
        )

    def delete_plan(self, payload):
        return self._required_engine().market_control(
            "delete-plan", payload.model_dump(mode="json"),
        )

    def pair_delete_plan(self, payload):
        engine = self._required_engine()
        self.subscriptions.pause_for_deletion(payload.symbol, payload.product)
        return engine.market_control(
            "pair-delete-plan", payload.model_dump(mode="json"),
        )

    def configuration_plan(self, policy):
        engine = self._required_engine()
        proposal = engine.market_control(
            "configuration-plan", {"policy": policy.model_dump(mode="json")},
        )
        if proposal["requires_confirmation"]:
            self.subscriptions.pause_for_deletion(policy.symbol, policy.product)
        return proposal

    def save_configuration(self, policy, *, deletion_snapshot=None, confirmation=None):
        from .contracts import DataPolicy, PolicyTrimRequest

        with self.lock:
            proposal = self.configuration_plan(policy)
            prepared = DataPolicy.model_validate(proposal["policy"])
            if proposal["requires_confirmation"]:
                if (deletion_snapshot != proposal["deletion"]["snapshot"]
                        or confirmation != f"DELETE {prepared.symbol}"):
                    raise HTTPException(409, "开始日期调晚须重新预览并确认删除。")
                job = self.start(PolicyTrimRequest(
                    symbol=prepared.symbol, product=prepared.product, policy=prepared,
                    deletion_snapshot=proposal["file_snapshot"], confirmation=confirmation,
                ))
                return {"policy": prepared.model_dump(mode="json"), "job": job}
            return self.subscriptions._save(prepared, allow_later_start=True)

    def _required_engine(self):
        """Never fall back to API-local workers or persisted job records."""
        engine = getattr(self, "engine", None)
        if engine is None:
            raise HTTPException(503, "行情 Engine 未配置，无法执行或读取数据任务。")
        return engine

    def jobs(self, *, reconcile=True):
        result = self._required_engine().market_control("jobs")
        if reconcile:
            self.reconcile_jobs()
        return result

    def job(self, identity):
        result = self._required_engine().market_control("job", {"id": identity})
        if result["status"] == "succeeded":
            self.reconcile_jobs()
        return result

    def task_snapshot(self, selected=""):
        return self._required_engine().market_control("snapshot", {"selected": selected})

    def start(self, payload):
        engine = self._required_engine()
        if payload.action in {"delete_pair", "trim"}:
            # Business configuration is paused before Engine takes exclusive
            # deletion leases; Engine validates the authoritative snapshot.
            self.subscriptions.pause_for_deletion(payload.symbol, payload.product)
        return engine.market_control(
            "job/start", {"payload": payload.model_dump(mode="json", exclude_none=True)},
        )

    def retry(self, identity):
        return self._required_engine().market_control("job/retry", {"id": identity})

    def cancel(self, identity):
        return self._required_engine().market_control("job/cancel", {"id": identity})

    def close(self):
        # Only Platform business subscriptions are closed here. Active Engine
        # jobs and their durable recovery survive API restarts.
        self.subscriptions.close()
