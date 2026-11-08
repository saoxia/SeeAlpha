"""Business-backend client for the resident engine. Secrets stay in the request body."""

import json
from pathlib import Path

import httpx

from engine.connectors.binance.queries import AccountSnapshot, QueryError
from engine.runtime import EngineConflict, EngineRuntime, read_account_snapshot


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
    def __init__(self, root, url="inprocess", token="seealpha-local-engine", market_account_id="market-data", project=None):
        self.root = Path(root)
        self.url = url.rstrip("/")
        self.token = token
        self.market_account_id = str(market_account_id)
        self.remote = self.url.startswith("http")
        self.runtime = None if self.remote else EngineRuntime(self.root, project)
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def close(self):
        if self.runtime is not None:
            self.runtime.close()

    def _sync(self, method, path, timeout=60, **kwargs):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.request(method, self.url + path, headers=self.headers, **kwargs)
        except httpx.HTTPError as exc:
            raise OSError("交易引擎不可用") from exc
        if response.status_code == 409:
            raise EngineConflict(response.json().get("detail", "引擎拒绝了该操作。"))
        if response.status_code >= 400:
            raise QueryError(response.json().get("detail", "引擎请求失败。"))
        return response.json() if response.content else None

    async def _async(self, method, path, **kwargs):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.request(method, self.url + path, headers=self.headers, **kwargs)
        except httpx.HTTPError as exc:
            raise OSError("交易引擎不可用") from exc
        if response.status_code == 409:
            raise EngineConflict(response.json().get("detail", "引擎拒绝了该操作。"))
        if response.status_code >= 400:
            raise QueryError(response.json().get("detail", "引擎请求失败。"))
        return response.json() if response.content else None

    def _account_path(self, tenant_id, account_id):
        return f"/tenants/{tenant_id}/accounts/{account_id}"

    def ensure_tenant(self, tenant_id, purpose, status="active"):
        if self.runtime is not None:
            return self.runtime.ensure_tenant(tenant_id, purpose, status)
        return self._sync("PUT", f"/tenants/{tenant_id}", json={"purpose": purpose, "status": status})

    def export_tenant(self, tenant_id):
        if self.runtime is not None:
            return self.runtime.export_tenant(tenant_id)
        return self._sync("GET", f"/tenants/{tenant_id}/export")

    def drop_tenant(self, tenant_id, *, remove=False):
        if self.runtime is not None:
            return self.runtime.drop_tenant(tenant_id, remove=remove)
        return self._sync("POST", f"/tenants/{tenant_id}/drop", params={"remove": remove})

    def import_tenant(self, tenant_id, purpose, payload):
        if self.runtime is not None:
            return self.runtime.import_tenant(tenant_id, purpose, payload)
        return self._sync("POST", f"/tenants/{tenant_id}/import", json={"purpose": purpose, "payload": payload})

    def runnable(self, tenant_id, action):
        if self.runtime is not None:
            return self.runtime.assert_runnable(tenant_id, action)
        return self._sync("POST", f"/tenants/{tenant_id}/runnable", json={"action": action})

    def receive_strategy(self, tenant_id, payload):
        if self.runtime is not None:
            return self.runtime.receive_strategy(tenant_id, payload)
        return self._sync("POST", f"/tenants/{tenant_id}/strategy-loads", json=payload)

    def run_backtest(
        self, tenant_id, account_id, start, end, interval, starting_balance, quote_currency,
    ):
        if self.runtime is not None:
            return self.runtime.run_backtest(
                tenant_id, account_id, start, end, interval, starting_balance, quote_currency,
            )
        return self._sync(
            "POST", self._account_path(tenant_id, account_id) + "/backtests",
            timeout=600,
            json={
                "start": start, "end": end, "interval": interval,
                "starting_balance": str(starting_balance), "quote_currency": quote_currency,
            },
        )

    def latest_backtest(self, tenant_id, account_id):
        if self.runtime is not None:
            return self.runtime.latest_backtest(tenant_id, account_id)
        try:
            return self._sync("GET", self._account_path(tenant_id, account_id) + "/backtests/latest")
        except QueryError as error:
            if "还没有回测" in str(error):
                return None
            raise

    def register(self, account):
        creds = account["credentials"]
        tenant_id = account["tenant_id"]
        if self.runtime is not None:
            return self.runtime.register(
                tenant_id, account["id"], account["environment"], creds["apiKey"], creds["secret"]
            )
        return self._sync("POST", self._account_path(tenant_id, account["id"]) + "/connections", json={
            "environment": account["environment"], "api_key": creds["apiKey"], "api_secret": creds["secret"],
        })

    async def account_snapshot(self, account):
        if self.runtime is not None:
            return await self.runtime.account_snapshot(account)
        payload = await self._async(
            "POST", self._account_path(account["tenant_id"], account["id"]) + "/snapshots", json={
                "environment": account["environment"],
                "api_key": account["credentials"]["apiKey"],
                "api_secret": account["credentials"]["secret"],
            },
        )
        return AccountSnapshot(payload["balances"], payload["info"], payload["positions"], payload["observed_at"])

    async def manual_execute(self, account, symbol, command):
        if self.runtime is not None:
            return await self.runtime.manual_execute(account, symbol, command)
        return await self._async("POST", self._account_path(account["tenant_id"], account["id"]) + "/manual", json={
            "environment": account["environment"],
            "api_key": account["credentials"]["apiKey"],
            "api_secret": account["credentials"]["secret"],
            "symbol": symbol,
            "command": command,
        })

    async def command(self, account, action, values, fingerprint, client_order_id):
        if self.runtime is not None:
            return await self.runtime.command(account, action, values, fingerprint, client_order_id)
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
        if self.runtime is not None:
            return self.runtime.store.get_request(tenant_id, account_id, request_id)
        try:
            return self._sync("GET", self._account_path(tenant_id, account_id) + f"/requests/{request_id}")
        except QueryError as error:
            if "没有该请求" in str(error):
                return None
            raise

    def save_request(self, tenant_id, account_id, request_id, body):
        if self.runtime is not None:
            return self.runtime.save_request(tenant_id, account_id, request_id, body)
        return self._sync(
            "PUT", self._account_path(tenant_id, account_id) + f"/requests/{request_id}", json=body
        )

    def orders(self, tenant_id, account_id, environment):
        if self.runtime is not None:
            return self.runtime.store.orders(tenant_id, account_id, environment)
        return self._sync(
            "GET", self._account_path(tenant_id, account_id) + "/orders", params={"environment": environment}
        )["orders"]

    def gaps(self, tenant_id, account_id, scope=None):
        if self.runtime is not None:
            return self.runtime.store.gaps(tenant_id, account_id, scope)
        params = {} if scope is None else {"scope": scope}
        return self._sync("GET", self._account_path(tenant_id, account_id) + "/gaps", params=params)["gaps"]

    def note_gap(self, tenant_id, account_id, scope, gap_key, gap):
        if self.runtime is not None:
            return self.runtime.note_gap(tenant_id, account_id, scope, gap_key, gap)
        return self._sync("POST", self._account_path(tenant_id, account_id) + "/gaps", json={
            "scope": scope, "gap_key": gap_key, "gap": gap,
        })

    def note_worker(self, command, *, tenant_id, account_id=None):
        account_id = account_id or self.market_account_id
        if self.runtime is not None:
            return self.runtime.note_worker(tenant_id, account_id, command)
        return self._sync(
            "POST", self._account_path(tenant_id, account_id) + "/market/workers/note", json={"command": command}
        )

    def note_subscription(self, tenant_id, product, symbol, role, state, progress, account_id=None):
        account_id = account_id or self.market_account_id
        if self.runtime is not None:
            return self.runtime.note_subscription(
                tenant_id, account_id, product, symbol, role, state, progress
            )
        return self._sync("POST", self._account_path(tenant_id, account_id) + "/market/subscriptions", json={
            "product": product, "symbol": symbol, "role": role, "state": state, "progress": progress,
        })

    def subscriptions(self, *, tenant_id, account_id=None):
        account_id = account_id or self.market_account_id
        if self.runtime is not None:
            return self.runtime.store.subscriptions(tenant_id, account_id)
        return self._sync(
            "GET", self._account_path(tenant_id, account_id) + "/market/subscriptions"
        )["subscriptions"]

    def spawn_worker(
        self, command, log_path, *, tenant_id, access=None, hold_lease=False, log_mode="ab", account_id=None,
    ):
        account_id = account_id or self.market_account_id
        if self.runtime is not None:
            started = self.runtime.spawn_worker(
                command, log_path, tenant_id=tenant_id, access=access, hold_lease=hold_lease, log_mode=log_mode,
            )
            process = self.runtime.workers[started["id"]]["process"]
            return process
        started = self._sync("POST", self._account_path(tenant_id, account_id) + "/market/workers", json={
            "command": command, "log_path": str(log_path),
            "access": list(access) if access else None,
            "hold_lease": hold_lease, "log_mode": log_mode,
        })
        return RemoteProcess(self, started["id"], started["pid"])

    def worker(self, worker_id):
        return self._sync("GET", f"/workers/{worker_id}")

    async def valuation_events(self, account):
        if self.runtime is not None:
            from engine.runtime import valuation_events

            async for event in valuation_events(account):
                yield event
            return
        payload = {
            "environment": account["environment"],
            "api_key": account["credentials"]["apiKey"],
            "api_secret": account["credentials"]["secret"],
        }
        async with httpx.AsyncClient(timeout=None) as client, client.stream(
            "POST",
            f"{self.url}/tenants/{account['tenant_id']}/accounts/{account['id']}/valuation",
            headers=self.headers,
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


async def account_snapshot(account, engine=None):
    if engine is not None and engine.remote:
        return await engine.account_snapshot(account)
    if engine is not None and engine.runtime is not None:
        return await engine.runtime.account_snapshot(account)
    return await read_account_snapshot(account)
