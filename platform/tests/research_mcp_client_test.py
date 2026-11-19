"""Engine MCP transport contract: no REST fallback and no Engine admin bearer."""
from __future__ import annotations

import json

import httpx
import pytest

from api.research.direct_engine import DirectResearchError
from api.research.grants import ResearchGrant
from api.research.mcp_engine import EngineMCPResearchClient


@pytest.fixture
def grant():
    return ResearchGrant(
        token="synthetic-task-grant", task_id="research-task", tenant_id="11",
        expires_at=1999999999, fingerprint="fingerprint",
    )


def test_native_mcp_tools_and_snapshot_binary_keep_scoped_grant(grant):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer synthetic-task-grant"
        assert request.url.host == "engine.development.internal"
        if request.url.path == "/research/mcp":
            assert request.method == "POST"
            assert request.headers["mcp-protocol-version"] == "2025-11-25"
            rpc = json.loads(request.content)
            assert rpc["method"] == "tools/call"
            name = rpc["params"]["name"]
            args = rpc["params"]["arguments"]
            if name == "list_datasets":
                assert args == {}
                outcome = {"datasets": []}
            elif name == "create_snapshot":
                assert args["dataset_id"] == "futures_um:ETHUSDT:1m"
                outcome = {"snapshot_id": "a" * 32}
            elif name == "get_snapshot":
                outcome = {
                    "snapshot_id": "a" * 32, "status": "ready", "format": "parquet",
                    "size_bytes": 3,
                    "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
                }
            elif name == "submit_backtest":
                assert args["execution"] == "simulation"
                assert args["strategy_ref"] == "account:5"
                outcome = {"backtest_id": "b" * 32, "status": "queued"}
            elif name == "get_backtest":
                assert args == {"backtest_id": "b" * 32}
                outcome = {"backtest_id": "b" * 32, "status": "succeeded"}
            elif name == "cancel_backtest":
                outcome = {"backtest_id": "b" * 32, "status": "cancelled"}
            else:
                raise AssertionError(f"Unknown tool: {name}")
            return httpx.Response(200, headers={"Content-Type": "application/json"}, json={
                "jsonrpc": "2.0", "id": 1,
                "result": {"structuredContent": outcome, "content": []},
            })
        assert request.url.path == "/research/v1/snapshots/" + "a" * 32 + "/content"
        return httpx.Response(200, content=b"abc", headers={
            "Content-Type": "application/octet-stream",
            "X-SeeAlpha-Engine-Contract-Version": "2",
            "X-SeeAlpha-Research-Contract-Version": "1",
        })

    with EngineMCPResearchClient(
        "https://engine.development.internal", grant,
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.discover_datasets() == {"datasets": []}
        assert client.create_snapshot("futures_um:ETHUSDT:1m", "2026-01-01", "2026-01-02") == {
            "snapshot_id": "a" * 32,
        }
        assert client.fetch_snapshot("a" * 32) == b"abc"
        assert client.submit_backtest({
            "strategy_ref": "account:5", "dataset_refs": ["futures_um:ETHUSDT:1m"],
            "start": "2026-01-01", "end": "2026-01-02",
            "starting_balance": "1000", "quote_currency": "USDT",
            "parameters": {}, "idempotency_key": "one",
        })["status"] == "queued"
        assert client.get_backtest("b" * 32)["status"] == "succeeded"
        assert client.cancel_backtest("b" * 32)["status"] == "cancelled"
    assert sum(x.url.path == "/research/mcp" for x in requests) == 6
    assert not any(x.url.path == "/research/v1/datasets" for x in requests)


@pytest.mark.parametrize("response", [
    httpx.Response(404),
    httpx.Response(302, headers={"Location": "https://evil.invalid"}),
    httpx.Response(200, headers={"Content-Type": "application/json"}, json={
        "jsonrpc": "2.0", "id": 1,
        "result": {"isError": True, "content": [{"type": "text", "text": "secret"}]},
    }),
    httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=b"data: hello"),
    httpx.Response(200, headers={"Content-Type": "application/json"}, json={
        "jsonrpc": "2.0", "id": 2,
        "result": {"structuredContent": {"datasets": []}},
    }),
])
def test_mcp_failure_fails_closed_without_rest_fallback(grant, response):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        return response

    with EngineMCPResearchClient(
        "http://127.0.0.1:8766", grant,
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(DirectResearchError) as rejected:
            client.discover_datasets()
        assert "secret" not in str(rejected.value)
    assert paths == ["/research/mcp"]


def test_mcp_rejects_unsupported_method_or_identifier(grant):
    with EngineMCPResearchClient("http://127.0.0.1:8766", grant) as client:
        with pytest.raises(ValueError):
            client._request("GET", "/admin/v1/accounts")
        with pytest.raises(ValueError):
            client.get_backtest("../etc/passwd")
