"""Platform-owned subscription intent; Engine alone runs feed and history workers."""

import asyncio
import fcntl
import threading

from fastapi import HTTPException

from .contracts import DataPolicy, option_pair


class Subscriptions:
    def __init__(self, manager):
        self.manager = manager
        self.project = manager.project
        self.lock = threading.RLock()
        self.policies = None
        self.last_error = None
        self.lease = None
        self.closed = False
        self.deleting = set()

    def bound_tenant_id(self):
        if getattr(self, "_bound_tenant_id", None) is None:
            from api.tenants.repository import TenantRepository

            if self.manager.policy_repository is None:
                raise HTTPException(503, "行情服务配置需要已迁移的 ORM 数据库")
            self._bound_tenant_id = TenantRepository(self.manager.policy_repository.database).require_default()["id"]
        return self._bound_tenant_id

    def _read(self, *, refresh=False):
        if self.policies is None or refresh:
            if self.manager.policy_repository is None:
                raise HTTPException(503, "行情服务配置需要已迁移的 ORM 数据库")
            try:
                values = self.manager.policy_repository.list(tenant_id=self.bound_tenant_id())
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
        path = self.project / ".local/run/api/market_policy_writer.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        lease = path.open("a")
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lease.close()
            raise HTTPException(409, "行情业务配置正在由另一个 API 进程修改，请稍后重试") from None
        self.lease = lease

    def restore(self):
        """Reconcile ORM policy intent into the resident Engine, never spawn locally."""
        from api.db.errors import StorageUnavailable

        with self.lock:
            try:
                policies = self._read(refresh=True)
                result = self._engine().market_control("sync", {
                    "policies": [p.model_dump(mode="json") for p in policies],
                })
                self.last_error = result.get("last_error")
            except (OSError, HTTPException, StorageUnavailable, ValueError) as exc:
                self.last_error = str(exc.detail if isinstance(exc, HTTPException) else exc)

    def _engine(self):
        engine = getattr(self.manager, "engine", None)
        if engine is None:
            raise HTTPException(503, "行情 Engine 未配置，不能修改或读取订阅。")
        return engine

    async def sync_options(self, exchanges):
        """Discover contracts for persisted underlying scopes using the cached catalogue."""
        from api.db.errors import StorageUnavailable
        try:
            policies = await asyncio.to_thread(self._read)
            if not any(p.product == "options" and "-" not in p.symbol for p in policies):
                return
            markets = await exchanges.catalog("production", product="options")
            await asyncio.to_thread(self._include_options, markets)
        except HTTPException as exc:
            self.last_error = str(exc.detail)
        except (StorageUnavailable, OSError, ValueError, RuntimeError, TimeoutError):
            self.last_error = "期权目录同步失败；保留原配置，稍后重试"

    def _include_options(self, markets):
        with self.lock:
            self._claim()
            policies = self._read(refresh=True)
            scopes = {p.symbol: p for p in policies if p.product == "options" and "-" not in p.symbol}
            if not scopes:
                return
            known = {(p.product, p.symbol) for p in policies} | self.deleting
            configured = {p.symbol: p for p in policies if p.product == "options"}
            additions, metadata = [], {}
            for symbol, market in {item["symbol"]: item for item in markets}.items():
                previous = configured.get(symbol)
                pair = market.get("underlying") or (previous.underlying if previous else None) or option_pair(symbol)
                scope = scopes.get(pair, scopes.get("ALL"))
                if scope is None or ("options", pair) in self.deleting:
                    continue
                if pair not in scopes:
                    scope = DataPolicy.model_validate({**scope.model_dump(), "symbol": pair})
                    scopes[pair] = scope
                    additions.append(scope)
                if ("options", symbol) not in known:
                    additions.append(DataPolicy.model_validate({**scope.model_dump(), "symbol": symbol, "underlying": pair}))
                metadata[symbol] = pair
            updated = [p.model_copy(update={"underlying": metadata[p.symbol]})
                       if p.product == "options" and p.symbol in metadata else p for p in policies]
            if additions or updated != policies:
                self._write([*updated, *additions])
            if self.last_error == "期权目录同步失败；保留原配置，稍后重试":
                self.last_error = None

    def save(self, policy):
        # Engine /sync validates deletion exclusion while holding tenant
        # volume access leases. Platform never opens Engine filesystem locks.
        with self.lock:
            return self._save(policy)

    def _save(self, policy, *, allow_later_start=False):
        if policy.realtime_enabled or policy.history_enabled:
            self.manager.require_storage(write=True)
        with self.lock:
            self._claim()
            policies = self._read(refresh=True)
            key = (policy.product, policy.symbol)
            if key in self.deleting or (policy.product == "options" and ("options", policy.pair) in self.deleting):
                raise HTTPException(409, "该交易对正在删除，请等待任务完成")
            old = next((item for item in policies if (item.product, item.symbol) == key), None)
            if old is not None and policy.product == "options" and "-" in policy.symbol:
                policy = policy.model_copy(update={"underlying": old.underlying or policy.underlying})
            if (policy.product == "futures_um" and not allow_later_start and old is not None and old.history_mode == "start" and old.history_start is not None
                    and policy.history_start is not None and policy.history_start > old.history_start):
                raise HTTPException(409, "开始日期调晚须先预览并确认删除旧数据")
            if policy.product == "options" and "-" not in policy.symbol:
                # A user save applies the scope to existing contracts in one transaction.
                if any(product == "options" and (policy.symbol == "ALL" or symbol == policy.symbol
                       or any(p.symbol == symbol and p.pair == policy.symbol for p in policies))
                       for product, symbol in self.deleting):
                    raise HTTPException(409, "期权合约正在删除，完成后再调整范围配置")
                updated = [DataPolicy.model_validate({**policy.model_dump(), "symbol": item.symbol, "underlying": item.underlying})
                           if item.product == "options" and (policy.symbol == "ALL" or item.pair == policy.symbol)
                           else item for item in policies if (item.product, item.symbol) != key]
                self._write([*updated, policy])
                return self._public(policy)
            policies = [item for item in policies if (item.product, item.symbol) != key] + [policy]
            self._write(policies)
            return self._public(policy)

    def _write(self, policies):
        """Commit desired policies, then reconcile Engine-owned workers.

        If Engine sync fails after the ORM commit, periodic restore retries
        the same durable intent. No API worker can be launched.
        """
        if self.manager.policy_repository is None:
            raise HTTPException(503, "行情服务配置需要已迁移的 ORM 数据库")
        engine = self._engine()
        previous = self._read(refresh=True)
        self.manager.policy_repository.save(policies, tenant_id=self.bound_tenant_id())
        self.policies = list(policies)
        try:
            engine.market_control("sync", {
                "policies": [p.model_dump(mode="json") for p in policies],
            })
        except Exception:
            # A rejected Engine sync (including a held deletion lease) must
            # not leave the business database claiming a policy change that
            # the Engine refused to apply.
            try:
                self.manager.policy_repository.save(previous, tenant_id=self.bound_tenant_id())
                self.policies = list(previous)
            except Exception as rollback_error:
                self.policies = None  # Force a fresh database read for callers.
                raise HTTPException(
                    503, "行情订阅同步失败，业务配置回滚也失败；请核验数据库与 Engine 状态。",
                ) from rollback_error
            raise

    def pause_for_deletion(self, symbol, product):
        with self.lock:
            self._claim()
            policies = self._read(refresh=True)
            if product == "options" and "-" not in symbol:
                members = [p for p in policies if p.product == product and p.pair == symbol]
            else:
                members = [p for p in policies if (p.product, p.symbol) == (product, symbol)]
            if (product, symbol) in self.deleting or any((product, p.symbol) in self.deleting for p in members):
                raise HTTPException(409, "该交易对正在删除，请等待任务完成")
            if members:
                self._write([p.model_copy(update={"realtime_enabled": False, "history_enabled": False})
                             if p in members else p for p in policies])

    def remove(self, symbol, product):
        with self.lock:
            self._claim()
            policies = self._read()
            group = product == "options" and "-" not in symbol
            if group:
                # Removing ALL discovery must retain discovery for other existing
                # underlyings, including legacy configurations not yet expanded.
                scope = next((p for p in policies if p.product == "options" and p.symbol == "ALL"), None)
                groups = {p.symbol for p in policies if p.product == "options" and "-" not in p.symbol}
                for policy in list(policies):
                    if policy.product == "options" and "-" in policy.symbol and policy.pair not in groups:
                        source = scope or policy
                        policies.append(DataPolicy.model_validate({**source.model_dump(), "symbol": policy.pair, "underlying": None}))
                        groups.add(policy.pair)
            self._write([p for p in policies if not (p.product == product and
                         (p.symbol == symbol or group and p.pair in {symbol, "ALL"}))])

    def _public(self, policy):
        engine = self._engine()
        rows = engine.market_control("subscriptions", {"group_options": False})["items"]
        return next(
            (item for item in rows if item["policy"]["product"] == policy.product
             and item["policy"]["symbol"] == policy.symbol),
            {"policy": policy.model_dump(mode="json")},
        )

    def items(self, *, group_options=False):
        return self._engine().market_control(
            "subscriptions", {"group_options": group_options},
        )["items"]

    def close(self):
        """Release only Platform's policy writer lease, never Engine workers."""
        with self.lock:
            self.closed = True
            if self.lease:
                self.lease.close()
                self.lease = None
