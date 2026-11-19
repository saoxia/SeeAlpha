"""Task MCP proxy enforces the same live lease as the CLI research commands."""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

from api.research import task_router
from api.research.task_access import ResearchLeases
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_mcp_handshake_discovery_calls_and_revocation(tmp_path, monkeypatch):
    task_id = str(uuid4())
    leases = ResearchLeases(tmp_path / "private" / "leases.sqlite3")
    token, _ = leases.issue(
        task_id=task_id, owner_id=7, tenant_id=11, account_id="5",
        dataset_ref="futures_um:ETHUSDT:1m", turn=1,
    )
    app = FastAPI()
    app.state.research_leases = leases
    app.state.repository = SimpleNamespace(
        get_user_by_id=lambda key: {"username": "owner"} if key == 7 else None,
    )
    app.state.settings = SimpleNamespace(research_python_enabled=False)
    app.include_router(task_router.router, prefix="/api")
    status = {"state": "running", "turn": 1}
    calls = []

    @contextmanager
    def fake_harness(_request, _actor):
        class Harness:
            def get(self, _task_id):
                return {"status": status["state"], "turn": status["turn"]}
        yield Harness()

    def fake_command(_app, lease, operation, arguments):
        assert lease["tenant_id"] == 11
        assert lease["account_id"] == "5"
        calls.append((operation, arguments))
        if operation == "datasets":
            return {"datasets": []}
        if operation == "backtest_submit":
            return {"backtest_id": "b" * 32, "status": "queued"}
        raise AssertionError("Unexpected research operation")

    monkeypatch.setattr(task_router, "_client", fake_harness)
    monkeypatch.setattr(task_router, "_require_harness_owner", lambda *_: None)
    monkeypatch.setattr(task_router, "research_command", fake_command)
    path = f"/api/research/tasks/{task_id}/mcp"
    headers = {
        "Authorization": "Bearer " + token,
        "MCP-Protocol-Version": "2025-11-25",
        "Accept": "application/json, text/event-stream",
    }
    def send(client, identity, method, params=None, hdrs=None):
        return client.post(path, headers=headers if hdrs is None else hdrs, json={
            "jsonrpc": "2.0", "id": identity, "method": method,
            "params": params or {},
        })

    with TestClient(app) as client:
        missing = send(client, 1, "initialize", {
            "protocolVersion": "2025-11-25",
        }, {"Accept": "application/json"})
        assert missing.status_code == 401
        assert send(client, 1, "initialize", {
            "protocolVersion": "2025-11-25",
        }).json()["result"]["protocolVersion"] == "2025-11-25"
        assert client.post(path, headers=headers, json={
            "jsonrpc": "2.0", "method": "notifications/initialized",
        }).status_code == 202
        tools = send(client, 2, "tools/list").json()["result"]["tools"]
        names = {tool["name"] for tool in tools}
        assert "list_datasets" in names
        assert "submit_backtest" in names
        assert "submit_python_candidate_backtest" not in names
        ok = send(client, 3, "tools/call", {
            "name": "submit_backtest", "arguments": {
                "start": "2026-01-01", "end": "2026-01-02",
                "starting_balance": "1000", "quote_currency": "USDT",
                "idempotency_key": "candidate-1",
            },
        })
        assert ok.json()["result"]["structuredContent"]["status"] == "queued"
        assert calls[0][0] == "backtest_submit"
        assert send(client, 4, "tools/call", {
            "name": "execute_shell", "arguments": {"command": "id"},
        }).json()["error"]["code"] == -32602
        assert send(client, 5, "tools/call", {
            "name": "create_snapshot", "arguments": {
                "start": "2026-01-01", "end": "2026-01-02", "tenant_id": 999,
            },
        }).json()["result"]["isError"] is True
        assert send(client, 6, "tools/call", {
            "name": "submit_python_candidate_backtest", "arguments": {},
        }).json()["error"]["code"] == -32602
        assert send(client, 7, "tools/list", hdrs={**headers, "Origin": "https://evil.test"}).status_code == 403
        assert send(client, 8, "tools/list", hdrs={**headers, "MCP-Protocol-Version": "2026-07-28"}).status_code == 400
        status["turn"] = 2
        assert send(client, 9, "tools/list").status_code == 403
        status["turn"] = 1
        status["state"] = "cancelled"
        assert send(client, 10, "tools/list").status_code == 409
        status["state"] = "running"
        leases.revoke(task_id)
        assert send(client, 11, "tools/list").status_code == 403


def test_mcp_refuses_invalid_and_oversized_rpc_early(tmp_path):
    app = FastAPI()
    app.include_router(task_router.router, prefix="/api")
    path = f"/api/research/tasks/{uuid4()}/mcp"
    with TestClient(app) as client:
        assert client.post(path, content="x" * (512 * 1024 + 1), headers={
            "Content-Type": "application/json",
        }).status_code == 413
        assert client.post(path, content="{", headers={
            "Content-Type": "application/json",
        }).json()["error"]["code"] == -32700
