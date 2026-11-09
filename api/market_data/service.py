"""Business market-data configuration and Engine-owned task orchestration."""

import fcntl
import hashlib
import json
import os
import re
import shutil
import threading
from contextlib import ExitStack
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException

from core.paths import PROJECT_ROOT

from .repository import PolicyRepository


def load_settings():
    from engine.pipeline.config import load_settings as load_native_settings

    return load_native_settings()


def checked_request(*args, **kwargs):
    from engine.pipeline.data_management import checked_request as checked_native_request

    return checked_native_request(*args, **kwargs)


def coverage(*args, **kwargs):
    from engine.pipeline.data_management import coverage as native_coverage

    return native_coverage(*args, **kwargs)


def deletion_plan(*args, **kwargs):
    from engine.pipeline.data_management import deletion_plan as native_deletion_plan

    return native_deletion_plan(*args, **kwargs)


def plan_daily_archives(*args, **kwargs):
    from engine.pipeline.sync_binance_batch import plan_daily_archives as native_plan

    return native_plan(*args, **kwargs)


class DataService:
    def __init__(self, database, *, project=PROJECT_ROOT):
        self.project = project
        self.policy_repository = PolicyRepository(database) if database.backend == "orm" else None
        self.lock = threading.RLock()
        from .subscriptions import Subscriptions
        self.subscriptions = Subscriptions(self)
        from .monitor import ServiceMonitor
        self.monitor = ServiceMonitor(self)

    @property
    def remote(self):
        return bool(getattr(self, "engine", None) is not None and self.engine.remote)

    def reconcile_jobs(self):
        """Apply Engine-terminal policy changes to the business database once."""
        if not self.remote:
            return
        from .contracts import DataPolicy
        for job in self.engine.market_control("jobs"):
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

    @property
    def directory(self):
        return self.project / ".local/state/api/data_jobs"

    @property
    def log_directory(self):
        return self.project / "logs/api/data"

    def storage(self):
        if self.remote:
            return self.engine.market_control("storage")
        checked_at = datetime.now(UTC).isoformat()
        data = self.project / "data"
        if not data.is_dir():
            return {"available": False, "writable": False, "free_bytes": None,
                    "checked_at": checked_at, "message": "数据目录或挂载盘不可用；不会自动创建替代目录。"}
        settings = load_settings()
        try:
            anchors = []
            for path in (settings.catalog_dir, settings.raw_dir, settings.state_dir, data):
                while not path.exists():
                    if path.is_symlink():
                        raise OSError("配置目录链接失效")
                    path = path.parent
                if not path.is_dir():
                    raise OSError("配置目录不是文件夹")
                anchors.append(path)
            return {"available": True, "writable": all(os.access(p, os.W_OK) for p in anchors),
                    "free_bytes": min(shutil.disk_usage(p).free for p in anchors),
                    "checked_at": checked_at, "message": None}
        except OSError:
            return {"available": False, "writable": False, "free_bytes": None,
                    "checked_at": checked_at, "message": "数据存储无法访问，请检查挂载与权限。"}

    def require_storage(self, *, write=False):
        state = self.storage()
        if not state["available"] or write and not state["writable"]:
            raise HTTPException(503, state["message"] or "数据存储不可写")

    def request(self, payload):
        settings = load_settings()
        request = checked_request(payload.symbol, payload.start, payload.end,
                                  payload.intervals, settings, market=payload.product)
        if request.market == "futures_um":
            plan_daily_archives(request)
        return request, settings

    def plan(self, payload):
        if self.remote:
            return self.engine.data_query("prepare-plan", payload.model_dump(mode="json"))
        self.require_storage()
        request, settings = self.request(payload)
        if request.market == "options":
            from engine.pipeline.options_data import check_download
            check_download(request)
        result = coverage(request, settings)
        return {**result, "archive_days": len(result["days"]),
                "missing_days": sum(not item["catalog_present"] for item in result["days"]),
                "request": payload.model_dump(mode="json"),
                "message": ("期权 Tick 获取官方近期接口返回的最多 500 笔，实际时间以记录为准；不代表完整历史。"
                            if request.market == "options" and not request.bar_intervals else
                            "期权历史 K 线直接来自 Binance；不从 K 线反推 Tick，不证明历史逐笔完整。"
                            if request.market == "options" else
                            "按完整 UTC 日归档处理；只补缺失，不强制重建。下载体积与耗时未知。")}

    def delete_plan(self, payload):
        if self.remote:
            return self.engine.market_control("delete-plan", payload.model_dump(mode="json"))
        self.require_storage(write=True)
        request, settings = self.request(payload)
        with self.lock:
            lease = self._lease(payload.symbol, payload.product)
            try:
                return deletion_plan(request, settings, delete_archives=payload.delete_archives)
            finally:
                lease.close()

    def _lease(self, symbol, product="futures_um", *, access=False, shared=False):
        from .contracts import normalize_scope
        symbol = normalize_scope(symbol, product)
        kind = "access" if access else "job"
        # Engine and API operate on the *same* mounted data volume, not on
        # distinct /app/.local/run and host-side runtime directories.
        data = self.project / "data"
        if not data.is_dir():
            raise HTTPException(503, "数据存储目录不可用。")
        path = data / ".locks" / product / f"{symbol}.{kind}.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        target = path.open("a")
        try:
            fcntl.flock(target, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            target.close()
            message = ("该交易对正在删除，请稍后操作。" if shared else
                       "该交易对仍有采集或数据读写，请先停止后再删除。" if access else
                       "该交易对已有数据任务运行；其他交易对仍可操作。")
            raise HTTPException(409, message) from None
        return target

    def pair_delete_plan(self, payload):
        if self.remote:
            self.subscriptions.pause_for_deletion(payload.symbol, payload.product)
            return self.engine.market_control("pair-delete-plan", payload.model_dump(mode="json"))
        from engine.pipeline.pair_deletion import pair_deletion_plan
        self.require_storage(write=True)
        with self.lock, self.subscriptions.lock:
            self.subscriptions.pause_for_deletion(payload.symbol, payload.product)
            with self._lease(payload.symbol, payload.product), self._lease(payload.symbol, payload.product, access=True):
                return pair_deletion_plan(payload.symbol, payload.product, load_settings(), project=self.project)

    def configuration_plan(self, policy):
        """Resolve a desired start; later starts require an exclusive deletion plan."""
        if self.remote:
            proposal = self.engine.market_control(
                "configuration-plan", {"policy": policy.model_dump(mode="json")},
            )
            if proposal["requires_confirmation"]:
                self.subscriptions.pause_for_deletion(policy.symbol, policy.product)
            return proposal
        from engine.pipeline.pair_deletion import history_trim_plan
        with self.lock, self.subscriptions.lock:
            self.subscriptions._claim()
            old = next((item for item in self.subscriptions._read(refresh=True)
                        if (item.product, item.symbol) == (policy.product, policy.symbol)), None)
            policy = self.subscriptions.resolve(policy, old)
            later = False
            if policy.product == "futures_um" and policy.history_start is not None:
                if policy.history_start >= datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0):
                    raise ValueError("数据开始日期须早于今天 UTC 零点")
                if old is not None and old.history_mode == "start" and old.history_start is not None:
                    later = policy.history_start > old.history_start
                else:
                    # Enabling history must not stop a collector when the desired
                    # start is earlier than all retained data, including raw archives.
                    from dataclasses import replace

                    from core.market.instruments import INTERVALS, bar_window
                    from engine.feed.pair_store import journal_inventory
                    from engine.pipeline.data_management import dataset_inventory
                    from engine.pipeline.pair_deletion import pair_deletion_plan
                    self.require_storage(write=True)
                    settings = load_settings()
                    items = dataset_inventory(replace(settings, intervals=tuple(INTERVALS)), all_levels=True) if settings.catalog_dir.is_dir() else []
                    items += [item for path in self.subscriptions.journal_paths(old or policy)
                              for item in journal_inventory(path, policy.symbol)]
                    for item in items:
                        if item["symbol"] != policy.symbol or item.get("product", "futures_um") != policy.product:
                            continue
                        for first, _ in item["event_ranges_ns"]:
                            stamp = int(first) // 1000000
                            opening = stamp if item["level"] == "tick" else bar_window(item["level"], stamp)[0]
                            later |= opening < int(policy.history_start.timestamp() * 1000)
                    for item in pair_deletion_plan(policy.symbol, policy.product, settings, project=self.project)["files"]:
                        match = re.search(r"-(\d{4}-\d{2}(?:-\d{2})?)\.zip", item["path"])
                        if item["group"] == "archives" and match:
                            value = match[1]
                            begin = datetime.fromisoformat(value + ("-01" if len(value) == 7 else "")).replace(tzinfo=UTC)
                            later |= begin < policy.history_start
                        elif item["group"] == "archives":
                            later = True  # Unknown archive dates require an explicit retention review.
            if not later:
                return {"policy": policy.model_dump(mode="json"), "requires_confirmation": False, "deletion": None}
            self.require_storage(write=True)
            self.subscriptions.pause_for_deletion(policy.symbol, policy.product)
            with self._lease(policy.symbol, policy.product), self._lease(policy.symbol, policy.product, access=True):
                plan = history_trim_plan(policy.symbol, policy.product, policy.history_start, load_settings(), project=self.project)
                paused = next((item.model_dump(mode="json") for item in self.subscriptions._read()
                               if (item.product, item.symbol) == (policy.product, policy.symbol)), None)
                file_snapshot = plan["snapshot"]
                plan["snapshot"] = hashlib.sha256(json.dumps({"files": file_snapshot,
                    "policy": policy.model_dump(mode="json"), "previous": paused}, sort_keys=True).encode()).hexdigest()
            return {"policy": policy.model_dump(mode="json"), "requires_confirmation": bool(plan["file_count"] or plan["retained_files"]),
                    "deletion": plan, "file_snapshot": file_snapshot}

    def save_configuration(self, policy, *, deletion_snapshot=None, confirmation=None):
        from .contracts import DataPolicy, PolicyTrimRequest
        if self.remote:
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
        with self.lock, self.subscriptions.lock:
            proposal = self.configuration_plan(policy)
            policy = DataPolicy.model_validate(proposal["policy"])
            if proposal["requires_confirmation"]:
                if deletion_snapshot != proposal["deletion"]["snapshot"] or confirmation != f"DELETE {policy.symbol}":
                    raise HTTPException(409, "开始日期调晚须先预览并确认删除；数据变化后须重新预览")
                return {"policy": policy.model_dump(mode="json"), "job": self.start(PolicyTrimRequest(
                    symbol=policy.symbol, product=policy.product, policy=policy,
                    deletion_snapshot=proposal["file_snapshot"], confirmation=confirmation))}
            if policy.product == "options" and policy.symbol == "ALL":
                # Scope parameters have no market files and must not acquire a contract lease.
                return self.subscriptions._save(policy)
            if (not (self.project / "data").is_dir()
                    and not policy.realtime_enabled and not policy.history_enabled
                    and proposal["deletion"] is None):
                # Disabling/configuring a stopped feed only updates the business
                # policy. It must be possible while the market-data disk is offline.
                return self.subscriptions._save(policy, allow_later_start=True)
            with ExitStack() as lease:
                if proposal["deletion"] is not None:
                    lease.enter_context(self._lease(policy.symbol, policy.product))
                lease.enter_context(self._lease(policy.symbol, policy.product, access=True, shared=True))
                return self.subscriptions._save(policy, allow_later_start=True)

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
