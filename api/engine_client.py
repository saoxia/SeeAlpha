"""Business-backend client for the resident engine. Secrets stay in the request body."""

import json
import re
from decimal import Decimal
from pathlib import Path

import httpx

from core.engine_contracts import AccountSnapshot, EngineConflict, QueryError


class RemoteProcess:
    def __init__(self, client, worker_id, pid):
        self.client = client
        self.worker_id = worker_id
        self.pid = pid
        self.returncode = None

    def poll(self):
        state = self.client.worker(self.worker_id)
        self.pid = state.get("pid", self.pid)
        self.returncode = state.get("returncode")
        return self.returncode

    def wait(self, timeout=None):
        import subprocess
        import time

        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.worker_id, timeout)
            time.sleep(0.05)
        return self.returncode


class EngineClient:
    def __init__(
        self, root, url="http://127.0.0.1:8766", token="seealpha-local-engine",
        market_account_id="market-data", tenant_manager=None, *, sync_transport=None,
        async_transport=None,
    ):
        self.root = Path(root)
        self.url = url.rstrip("/")
        self.token = token
        self.market_account_id = str(market_account_id)
        self.tenant_manager = tenant_manager
        self._worker_tenants = {}
        if tenant_manager is None and not self.url.startswith(("http://", "https://")):
            raise ValueError("EngineClient 仅支持 HTTP(S) Engine 服务。")
        self.sync_transport = sync_transport
        self.async_transport = async_transport
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def close(self):
        """HTTP connections are scoped to each call; no Engine runtime is owned."""

    def _target(self, path, tenant_id=None):
        if self.tenant_manager is None:
            return self.url, self.headers
        match = re.match(r"^/tenants/([1-9][0-9]*)(?:/|$)", path)
        if tenant_id is None:
            if match is None:
                raise EngineConflict("无法确定 Engine 请求的租户。")
            tenant_id = match.group(1)
        elif match is not None and match.group(1) != str(tenant_id):
            raise EngineConflict("Engine 路由租户不匹配。")
        url, token = self.tenant_manager.endpoint(tenant_id)
        return url, {"Authorization": f"Bearer {token}"}

    def _sync(self, method, path, timeout=60, *, tenant_id=None, **kwargs):
        url, headers = self._target(path, tenant_id)
        try:
            with httpx.Client(timeout=timeout, transport=self.sync_transport) as client:
                response = client.request(method, url + path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise OSError("交易引擎不可用") from exc
        if response.status_code == 409:
            raise EngineConflict(response.json().get("detail", "引擎拒绝了该操作。"))
        if response.status_code >= 400:
            raise QueryError(response.json().get("detail", "引擎请求失败。"))
        return response.json() if response.content else None

    async def _async(self, method, path, **kwargs):
        url, headers = self._target(path)
        try:
            async with httpx.AsyncClient(timeout=120, transport=self.async_transport) as client:
                response = await client.request(method, url + path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise OSError("交易引擎不可用") from exc
        if response.status_code == 409:
            raise EngineConflict(response.json().get("detail", "引擎拒绝了该操作。"))
        if response.status_code >= 400:
            raise QueryError(response.json().get("detail", "引擎请求失败。"))
        return response.json() if response.content else None

    def _public_tenant(self):
        if self.tenant_manager is not None:
            tenant = self.tenant_manager.market_tenant_id
            if tenant is None:
                raise EngineConflict("Engine 默认租户尚未初始化。")
            return str(tenant)
        return "1"

    async def market_catalog(self, environment="production", product="futures_um"):
        result = await self._async(
            "POST", f"/tenants/{self._public_tenant()}/venue/catalog",
            json={"environment": environment, "product": product},
        )
        return result["items"]

    async def market_tickers(self, environment):
        return await self._async(
            "POST", f"/tenants/{self._public_tenant()}/venue/tickers",
            json={"environment": environment},
        )

    async def asset_price(self, environment, currency):
        result = await self._async(
            "POST", f"/tenants/{self._public_tenant()}/venue/asset-price",
            json={"environment": environment, "currency": currency},
        )
        return {**result, "price": Decimal(result["price"])}

    async def asset_account(self, account, *, positions=False):
        result = await self._async(
            "POST", f"/tenants/{account['tenant_id']}/venue/asset-account",
            json={
                "environment": account["environment"],
                "api_key": account["credentials"]["apiKey"],
                "api_secret": account["credentials"]["secret"],
                "account_id": str(account["id"]),
                "account_name": account.get("account_name", ""),
                "exchange": account.get("exchange", "binance"),
                "positions": positions,
            },
        )
        result["assets"] = {
            currency: {field: Decimal(value) for field, value in values.items()}
            for currency, values in result["assets"].items()
        }
        return result

    def data_query(self, operation, payload):
        """Call the configured market-data tenant's read-only Engine API."""
        from fastapi import HTTPException

        path = f"/tenants/{self._public_tenant()}/data/{operation}"
        url, headers = self._target(path)
        try:
            with httpx.Client(timeout=120, transport=self.sync_transport) as client:
                response = client.post(url + path, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise HTTPException(503, "租户数据引擎不可用。") from exc
        if response.status_code >= 400:
            detail = response.json().get("detail", "数据查询失败。")
            if response.status_code in {403, 404, 409, 422, 503}:
                raise HTTPException(response.status_code, detail)
            raise HTTPException(502, "Engine 数据查询失败。")
        return response.json()

    def market_control(self, operation, payload=None):
        return self.data_query("control/" + operation, payload or {})

    def market_export(self, identity):
        path = f"/tenants/{self._public_tenant()}/data/control/exports/{identity}"
        url, headers = self._target(path)
        try:
            with (httpx.Client(timeout=None, transport=self.sync_transport) as client,
                  client.stream("GET", url + path, headers=headers) as response):
                if response.status_code >= 400:
                    raise QueryError("导出文件不可用。")
                yield from response.iter_bytes(chunk_size=65536)
        except httpx.HTTPError as exc:
            raise OSError("租户 Engine 导出服务不可用。") from exc

    def _account_credentials(self, account):
        return {
            "environment": account["environment"],
            "api_key": account["credentials"]["apiKey"],
            "api_secret": account["credentials"]["secret"],
            "account_id": str(account["id"]),
            "account_name": account.get("account_name", ""),
            "exchange": account.get("exchange", "binance"),
        }

    async def operation_history(self, account, *, lookback_minutes=1440):
        result = await self._async(
            "POST", f"/tenants/{account['tenant_id']}/venue/operation-history",
            json={**self._account_credentials(account), "lookback_minutes": lookback_minutes},
        )
        return result["records"]

    async def _query_lines(self, account, route, payload):
        path = f"/tenants/{account['tenant_id']}/venue/{route}"
        url, headers = self._target(path)
        try:
            async with (httpx.AsyncClient(timeout=None, transport=self.async_transport) as client,
                        client.stream("POST", url + path, headers=headers, json=payload) as response):
                if response.status_code >= 400:
                    raise QueryError("交易所历史查询失败。")
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    event = json.loads(line)
                    if isinstance(event, dict) and "error" in event and len(event) == 1:
                        raise QueryError(event["error"])
                    yield event
        except httpx.HTTPError as exc:
            raise OSError("交易引擎不可用") from exc

    async def income_history(self, account, start_ms, end_ms):
        payload = {**self._account_credentials(account), "start_ms": start_ms, "end_ms": end_ms}
        async for page in self._query_lines(account, "income", payload):
            yield page

    def options_client(self, account):
        return RemoteOptionsClient(self, account)

    async def options_server_time(self, account):
        result = await self._async(
            "POST", f"/tenants/{account['tenant_id']}/venue/options/time",
            json={"environment": account["environment"]},
        )
        return result

    def _account_path(self, tenant_id, account_id):
        return f"/tenants/{tenant_id}/accounts/{account_id}"

    def ensure_tenant(self, tenant_id, purpose, status="active"):
        if self.tenant_manager is not None:
            self.tenant_manager.ensure(tenant_id, purpose, status)
            if status == "archived":
                return {"tenant_id": str(tenant_id), "status": status}
        return self._sync("PUT", f"/tenants/{tenant_id}", json={"purpose": purpose, "status": status})

    def export_tenant(self, tenant_id):
        return self._sync("GET", f"/tenants/{tenant_id}/export")

    def drop_tenant(self, tenant_id, *, remove=False):
        result = self._sync("POST", f"/tenants/{tenant_id}/drop", params={"remove": remove})
        if self.tenant_manager is not None:
            self.tenant_manager.stop(tenant_id, remove=True, retire=remove)
        return result

    def import_tenant(self, tenant_id, purpose, payload):
        if self.tenant_manager is not None:
            self.tenant_manager.ensure(tenant_id, purpose)
        return self._sync("POST", f"/tenants/{tenant_id}/import", json={"purpose": purpose, "payload": payload})

    def runnable(self, tenant_id, action):
        return self._sync("POST", f"/tenants/{tenant_id}/runnable", json={"action": action})

    def inspect_strategy(self, archive_b64, parameters, *, tenant_id=None):
        target = self._public_tenant() if tenant_id is None else str(tenant_id)
        return self._sync(
            "POST", f"/tenants/{target}/strategy-packages/inspect", tenant_id=target,
            timeout=45, json={"archive": archive_b64, "parameters": parameters},
        )

    def receive_strategy(self, tenant_id, payload):
        return self._sync("POST", f"/tenants/{tenant_id}/strategy-loads", json=payload)

    def run_backtest(
        self, tenant_id, account_id, start, end, interval, starting_balance, quote_currency,
    ):
        return self._sync(
            "POST", self._account_path(tenant_id, account_id) + "/backtests",
            timeout=600,
            json={
                "start": start, "end": end, "interval": interval,
                "starting_balance": str(starting_balance), "quote_currency": quote_currency,
            },
        )

    def _task_sync(self, method, tenant_id, suffix="", **kwargs):
        """Forward task HTTP statuses without masking a missing job as 502."""
        from fastapi import HTTPException

        path = f"/tenants/{tenant_id}/jobs{suffix}"
        url, headers = self._target(path)
        try:
            with httpx.Client(timeout=120, transport=self.sync_transport) as client:
                response = client.request(method, url + path, headers=headers, **kwargs)
        except httpx.HTTPError as error:
            raise HTTPException(503, "租户 Engine 任务服务不可用。") from error
        if response.status_code >= 400:
            if response.status_code in {403, 404, 409, 422, 429, 503}:
                try:
                    message = response.json().get("detail", "任务请求未被接受。")
                except ValueError:
                    message = "任务请求未被接受。"
                raise HTTPException(response.status_code, message)
            raise HTTPException(502, "Engine 任务接口调用失败。")
        return response.json() if response.content else None

    def submit_task(self, tenant_id, kind, parameters, *, idempotency_key=None):
        """Enqueue tenant work without waiting for CPU-heavy computation."""
        return self._task_sync(
            "POST", tenant_id,
            json={"kind": kind, "parameters": parameters, "idempotency_key": idempotency_key},
        )

    def list_tasks(self, tenant_id):
        return self._task_sync("GET", tenant_id)["items"]

    def task(self, tenant_id, identity):
        return self._task_sync("GET", tenant_id, f"/{identity}")

    def task_result(self, tenant_id, identity):
        return self._task_sync("GET", tenant_id, f"/{identity}/result")

    def cancel_task(self, tenant_id, identity):
        return self._task_sync("POST", tenant_id, f"/{identity}/cancel")

    def retry_task(self, tenant_id, identity):
        return self._task_sync("POST", tenant_id, f"/{identity}/retry")

    def latest_backtest(self, tenant_id, account_id):
        try:
            return self._sync("GET", self._account_path(tenant_id, account_id) + "/backtests/latest")
        except QueryError as error:
            if "还没有回测" in str(error):
                return None
            raise

    def register(self, account):
        creds = account["credentials"]
        tenant_id = account["tenant_id"]
        return self._sync("POST", self._account_path(tenant_id, account["id"]) + "/connections", json={
            "environment": account["environment"], "api_key": creds["apiKey"], "api_secret": creds["secret"],
        })

    async def account_snapshot(self, account):
        payload = await self._async(
            "POST", self._account_path(account["tenant_id"], account["id"]) + "/snapshots", json={
                "environment": account["environment"],
                "api_key": account["credentials"]["apiKey"],
                "api_secret": account["credentials"]["secret"],
            },
        )
        return AccountSnapshot(payload["balances"], payload["info"], payload["positions"], payload["observed_at"])

    async def manual_execute(self, account, symbol, command):
        return await self._async("POST", self._account_path(account["tenant_id"], account["id"]) + "/manual", json={
            "environment": account["environment"],
            "api_key": account["credentials"]["apiKey"],
            "api_secret": account["credentials"]["secret"],
            "symbol": symbol,
            "command": command,
        })

    async def command(self, account, action, values, fingerprint, client_order_id):
        return await self._async("POST", self._account_path(account["tenant_id"], account["id"]) + "/orders", json={
            "environment": account["environment"],
            "api_key": account["credentials"]["apiKey"],
            "api_secret": account["credentials"]["secret"],
            "action": action,
            "values": values,
            "fingerprint": fingerprint,
            "client_order_id": client_order_id,
        })

    def get_request(self, tenant_id, account_id, request_id):
        try:
            return self._sync("GET", self._account_path(tenant_id, account_id) + f"/requests/{request_id}")
        except QueryError as error:
            if "没有该请求" in str(error):
                return None
            raise

    def save_request(self, tenant_id, account_id, request_id, body):
        return self._sync(
            "PUT", self._account_path(tenant_id, account_id) + f"/requests/{request_id}", json=body
        )

    def orders(self, tenant_id, account_id, environment):
        return self._sync(
            "GET", self._account_path(tenant_id, account_id) + "/orders", params={"environment": environment}
        )["orders"]

    def gaps(self, tenant_id, account_id, scope=None):
        params = {} if scope is None else {"scope": scope}
        return self._sync("GET", self._account_path(tenant_id, account_id) + "/gaps", params=params)["gaps"]

    def note_gap(self, tenant_id, account_id, scope, gap_key, gap):
        return self._sync("POST", self._account_path(tenant_id, account_id) + "/gaps", json={
            "scope": scope, "gap_key": gap_key, "gap": gap,
        })

    def note_worker(self, command, *, tenant_id, account_id=None):
        account_id = account_id or self.market_account_id
        return self._sync(
            "POST", self._account_path(tenant_id, account_id) + "/market/workers/note", json={"command": command}
        )

    def note_subscription(self, tenant_id, product, symbol, role, state, progress, account_id=None):
        account_id = account_id or self.market_account_id
        return self._sync("POST", self._account_path(tenant_id, account_id) + "/market/subscriptions", json={
            "product": product, "symbol": symbol, "role": role, "state": state, "progress": progress,
        })

    def subscriptions(self, *, tenant_id, account_id=None):
        account_id = account_id or self.market_account_id
        return self._sync(
            "GET", self._account_path(tenant_id, account_id) + "/market/subscriptions"
        )["subscriptions"]

    def spawn_worker(
        self, command, log_path, *, tenant_id, access=None, hold_lease=False, log_mode="ab", account_id=None,
    ):
        account_id = account_id or self.market_account_id
        if self.tenant_manager is not None:
            # API Python paths do not exist inside Docker; use the image interpreter.
            if command and Path(command[0]).name.startswith("python"):
                command = ["python", *command[1:]]
            log_path = self.tenant_manager.worker_log(tenant_id, log_path)
        started = self._sync("POST", self._account_path(tenant_id, account_id) + "/market/workers", json={
            "command": command, "log_path": str(log_path),
            "access": list(access) if access else None,
            "hold_lease": hold_lease, "log_mode": log_mode,
        })
        if self.tenant_manager is not None:
            self._worker_tenants[started["id"]] = str(tenant_id)
        return RemoteProcess(self, started["id"], started["pid"])

    def worker(self, worker_id):
        tenant_id = self._worker_tenants.get(worker_id) if self.tenant_manager else None
        return self._sync("GET", f"/workers/{worker_id}", tenant_id=tenant_id)

    async def valuation_events(self, account):
        payload = {
            "environment": account["environment"],
            "api_key": account["credentials"]["apiKey"],
            "api_secret": account["credentials"]["secret"],
        }
        path = self._account_path(account["tenant_id"], account["id"]) + "/valuation"
        url, headers = self._target(path)
        async with httpx.AsyncClient(timeout=None, transport=self.async_transport) as client, client.stream(
            "POST",
            url + path,
            headers=headers,
            json=payload,
        ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    event = json.loads(line)
                    if "error" in event and len(event) == 1:
                        raise QueryError(event["error"])
                    yield event


async def account_snapshot(account, engine):
    """Read account information exclusively through the configured Engine API."""
    return await engine.account_snapshot(account)


class RemoteOptionsClient:
    """Scoped, read-only options client backed by the tenant Engine HTTP service."""

    def __init__(self, engine, account):
        self.engine = engine
        self.account = account

    async def _read(self, suffix, **values):
        account = self.account
        return await self.engine._async(
            "POST", f"/tenants/{account['tenant_id']}/venue/options/{suffix}",
            json={**self.engine._account_credentials(account), **values},
        )

    async def positions(self, symbol=None):
        return (await self._read("positions", symbol=symbol))["rows"]

    async def open_orders(self, symbol=None):
        return await self._read("open-orders", symbol=symbol)

    async def order_history(self, symbol, start_ms, end_ms):
        return await self._read("order-history", symbol=symbol, start_ms=start_ms, end_ms=end_ms)

    async def history_pages(self, endpoint, start_ms, end_ms, *, symbol=None):
        account = self.account
        payload = {
            **self.engine._account_credentials(account),
            "endpoint": endpoint, "start_ms": start_ms, "end_ms": end_ms, "symbol": symbol,
        }
        async for page in self.engine._query_lines(account, "options/pages", payload):
            yield page
