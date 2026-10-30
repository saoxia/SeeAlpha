"""Manage existing feed processes and their product/symbol settings, never market storage."""

import asyncio
import fcntl
import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

from .contracts import DataPolicy


class Subscriptions:
    def __init__(self, manager):
        self.manager = manager
        self.project = manager.project
        self.lock = threading.RLock()
        self.processes = {}
        self.auxiliary = {}
        self.policies = None
        self.last_error = None
        self.lease = None
        self.closed = False
        self.deleting = set()

    def _read(self, *, refresh=False):
        if self.policies is None or refresh:
            if self.manager.policy_repository is None:
                raise HTTPException(503, "行情服务配置需要已迁移的 ORM 数据库")
            try:
                values = self.manager.policy_repository.list()
                policies = [DataPolicy.model_validate(item) for item in values]
                if len({(p.product, p.symbol) for p in policies}) != len(policies):
                    raise ValueError("Duplicate policy")
            except (ValueError, TypeError):
                raise HTTPException(503, "数据库行情配置异常；不会自动重置") from None
            self.policies = policies
        return list(self.policies)

    def _claim(self):
        if self.closed:
            raise HTTPException(503, "服务正在停止")
        if self.lease is not None:
            return
        path = self.project / ".local/run/api/subscriptions.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        lease = path.open("a")
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lease.close()
            raise HTTPException(409, "实时采集由另一个 API 进程管理，请在所属服务操作") from None
        self.lease = lease

    def root(self, policy):
        root = self.project / "data/market_data/binance_um_1m/managed" / policy.symbol
        return root if policy.base_level == "auto" else root / policy.base_level

    def journal_paths(self, policy):
        from core.market.instruments import INTERVALS
        root = self.project / "data/market_data/binance_um_1m/managed" / policy.symbol
        # A new minimum never hides older retained datasets. Known names only;
        # do not walk arbitrary directories or recreate an unavailable volume.
        paths = [self.root(policy) / "collection.sqlite", *(root / level / "collection.sqlite" for level in ("tick", *INTERVALS))]
        return list(dict.fromkeys(path for path in paths if path.is_file())) if policy.product == "futures_um" else []

    def _start(self, policy):
        key = (policy.product, policy.symbol)
        roles = (["collection"] if policy.realtime_enabled else [])
        if policy.base_level != "auto" and policy.product == "futures_um":
            if policy.realtime_enabled or policy.history_enabled:
                roles.append("calculation")
            if policy.history_enabled:
                roles.append("history")
        directory = self.project / "logs/api/data/feed"
        directory.mkdir(parents=True, exist_ok=True)
        for role in roles:
            processes = self.processes if role == "collection" else self.auxiliary
            identity = key if role == "collection" else (*key, role)
            existing = processes.get(identity)
            if existing is not None and existing.poll() is None:
                continue
            with self.manager._lease(policy.symbol, policy.product, access=True, shared=True) as access_lease, (directory / f"{policy.symbol}-{role}.log").open("ab", buffering=0) as output:
                command = ([sys.executable, "-m", "pipeline.pair_data", "--service", "--root", str(self.root(policy)),
                            "--request", policy.model_dump_json()] if role == "history" else
                           [sys.executable, "-m", "feed.managed", "--symbol", policy.symbol,
                            "--data-root", str(self.project / "data"), "--root", str(self.root(policy)),
                            "--catalog", str(self.project / "data/catalog"), "--intervals", "1m",
                            *(["--policy", policy.model_dump_json(), "--role", role] if policy.base_level != "auto" else policy.intervals)])
                processes[identity] = subprocess.Popen(
                    command, cwd=self.project, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    start_new_session=True, pass_fds=(self.lease.fileno(), access_lease.fileno()),
                    env={**os.environ, "PYTHONUNBUFFERED": "1"},
                )

    def restore(self):
        from api.db.errors import StorageUnavailable
        with self.lock:
            try:
                previous = self.policies or []
                policies = self._read(refresh=True)
                if previous:
                    self._claim()
                    for old in previous:
                        key = (old.product, old.symbol)
                        new = next((p for p in policies if (p.product, p.symbol) == key), None)
                        if new != old:
                            same_collection = (new is not None and old.realtime_enabled and new.realtime_enabled
                                               and old.base_level == new.base_level and set(old.intervals) == set(new.intervals))
                            self._stop_pair(key, collection=not same_collection)
                if not any(p.realtime_enabled or p.history_enabled for p in policies):
                    self.last_error = None
                    return
                self.manager.require_storage(write=True)
                self._claim()
                errors = []
                for policy in policies:
                    if policy.realtime_enabled or policy.history_enabled:
                        try:
                            self._start(policy)
                        except (HTTPException, OSError) as exc:
                            errors.append(f"{policy.symbol}: {exc}")
                self.last_error = "；".join(errors) or None
            except (HTTPException, StorageUnavailable, OSError) as exc:
                self.last_error = str(exc.detail if isinstance(exc, HTTPException) else exc)
                # Retain the previous configuration and running services on outages.

    def _stop_pair(self, key, *, collection=True, auxiliary=True):
        if collection:
            process = self.processes.pop(key, None)
            if process is not None:
                self.manager._stop(process)
        if auxiliary:
            for identity in [value for value in self.auxiliary if value[:2] == key]:
                self.manager._stop(self.auxiliary.pop(identity))

    def save(self, policy):
        # Shared access coexists with historical work; only deletion excludes it.
        # Keep lock ordering consistent with delete: subscription state, then access.
        with self.lock, self.manager._lease(policy.symbol, policy.product, access=True, shared=True):
            return self._save(policy)

    def resolve(self, policy, old=None):
        """Resolve input choices once to the UTC start stored in configuration."""
        if policy.product == "futures_um" and policy.base_level == "auto":
            policy = DataPolicy.model_validate({**policy.model_dump(), "base_level": "1m"})
        if policy.history_enabled and policy.product == "futures_um":
            start = policy.history_start
            if policy.history_mode == "all":
                from connectors.binance.pair_data import PairREST
                try:
                    rows = asyncio.run(PairREST(symbols=(policy.symbol,)).get(
                        "klines", symbol=policy.symbol, interval="1m", startTime=0, limit=1))
                    if not rows:
                        raise ValueError("交易所没有可用历史")
                    start = datetime.fromtimestamp(int(rows[0][0]) / 1000, UTC)
                except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                    raise HTTPException(503, "无法确定交易所最早历史日期；配置未保存") from exc
            elif policy.history_mode == "duration":
                if old is not None and old.history_mode == "start" and old.history_start is not None:
                    start = old.history_start - timedelta(days=policy.history_days)
                else:
                    from feed.pair_store import journal_inventory
                    from pipeline.catalog import ParquetDataCatalog
                    from pipeline.config import load_settings
                    from pipeline.pair_data import history_bounds
                    settings = load_settings()
                    catalog = ParquetDataCatalog(settings.catalog_dir) if settings.catalog_dir.is_dir() else None
                    starts = [int(item["event_ranges_ns"][0][0])
                              for path in self.journal_paths(policy)
                              for item in journal_inventory(path, policy.symbol)
                              if item["level"] == policy.base_level and item["event_ranges_ns"]]
                    start, _ = history_bounds(policy.model_dump(mode="json"), catalog, datetime.now(UTC),
                                              retained_start_ns=min(starts, default=None))
            policy = policy.model_copy(update={
                "history_mode": "start", "history_start": start.replace(hour=0, minute=0, second=0, microsecond=0),
                "history_days": DataPolicy.model_fields["history_days"].default,
            })
        return policy

    def _save(self, policy, *, allow_later_start=False):
        if policy.realtime_enabled or policy.history_enabled:
            self.manager.require_storage(write=True)
        with self.lock:
            self._claim()
            policies = self._read(refresh=True)
            key = (policy.product, policy.symbol)
            if key in self.deleting:
                raise HTTPException(409, "该交易对正在删除，请等待任务完成")
            old = next((item for item in policies if (item.product, item.symbol) == key), None)
            policy = self.resolve(policy, old)
            if (not allow_later_start and old is not None and old.history_mode == "start" and old.history_start is not None
                    and policy.history_start is not None and policy.history_start > old.history_start):
                raise HTTPException(409, "开始日期调晚须先预览并确认删除旧数据")
            same_collection = (old is not None and old.realtime_enabled and policy.realtime_enabled
                               and old.base_level == policy.base_level
                               and set(old.intervals) == set(policy.intervals))
            policies = [item for item in policies if (item.product, item.symbol) != key] + [policy]
            self._write(policies)
            if not same_collection:
                self._stop_pair(key, auxiliary=False)
            if old != policy:
                self._stop_pair(key, collection=False)
            if policy.realtime_enabled or policy.history_enabled:
                try:
                    self._start(policy)
                    self.last_error = None
                except (OSError, HTTPException) as exc:
                    self.last_error = str(exc)
            return self._public(policy)

    def _write(self, policies):
        if self.manager.policy_repository is None:
            raise HTTPException(503, "行情服务配置需要已迁移的 ORM 数据库")
        self.manager.policy_repository.save(policies)
        self.policies = list(policies)

    def pause_for_deletion(self, symbol, product):
        with self.lock:
            self._claim()
            policies = self._read(refresh=True)
            policy = next((p for p in policies if (p.product, p.symbol) == (product, symbol)), None)
            if (product, symbol) in self.deleting:
                raise HTTPException(409, "该交易对正在删除，请等待任务完成")
            if policy is not None:
                paused = policy.model_copy(update={"realtime_enabled": False, "history_enabled": False})
                self._write([paused if (p.product, p.symbol) == (product, symbol) else p for p in policies])
                self._stop_pair((product, symbol))

    def remove(self, symbol, product):
        with self.lock:
            self._claim()
            self._write([p for p in self._read() if (p.product, p.symbol) != (product, symbol)])

    def _public(self, policy):
        state = {"state": "STOPPED", "data_ready": False, "reason": "not_started",
                 "bar_updates": [], "symbols": {}}
        if policy.product == "futures_um":
            path = self.root(policy) / "status.json"
            try:
                if path.is_file():
                    if path.stat().st_size > 131072:
                        raise ValueError("Oversize status")
                    value = json.loads(path.read_text())
                    if (not isinstance(value, dict) or value.get("state") not in
                            {"STOPPED", "READY", "DEGRADED", "WARMING_UP"}
                            or type(value.get("valid_until_ms")) is not int
                            or type(value.get("data_ready", False)) is not bool):
                        raise ValueError("Invalid status")
                    state = {key: value[key] for key in ("state", "data_ready", "reason", "checked_ms",
                             "valid_until_ms", "symbols", "job", "bar_updates", "last_error", "gap_count", "scope") if key in value}
                    if int(time.time() * 1000) > value["valid_until_ms"]:
                        state.update(state="DEGRADED", data_ready=False, reason="status_expired", bar_updates=[])
            except (OSError, ValueError, TypeError):
                state.update(state="DEGRADED", data_ready=False, reason="invalid_status", bar_updates=[])
        process = self.processes.get((policy.product, policy.symbol))
        if process is not None and process.poll() is not None:
            state.update(state="DEGRADED", data_ready=False, reason="process_exited", bar_updates=[])
        elif not policy.realtime_enabled:
            state.update(state="STOPPED", data_ready=False, reason="disabled", bar_updates=[])
        from .monitor import observe
        services = observe(self, policy, int(time.time() * 1000))
        state["bar_updates"] = services["calculation"].get("bar_updates", state.get("bar_updates", []))
        return {"policy": policy.model_dump(mode="json"), "runtime": state, "services": services}

    def items(self):
        # Configurations are replaced as a list, never mutated in place. Status
        # readers must not wait behind a cloud configuration refresh transaction.
        return [self._public(policy) for policy in self._read()]

    def close(self):
        with self.lock:
            self.closed = True
            for process in [*self.processes.values(), *self.auxiliary.values()]:
                self.manager._stop(process)
            self.processes.clear()
            self.auxiliary.clear()
            if self.lease:
                self.lease.close()
                self.lease = None
