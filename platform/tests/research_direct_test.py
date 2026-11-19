"""Direct Platform -> Engine research client tests; mocked transport, no credentials."""
import hashlib

import httpx
import pytest

from api.research.direct_engine import (
    DirectEngineResearchClient,
    DirectResearchError,
)
from api.research.grants import ResearchGrant

GRANT = ResearchGrant(
    token="scoped-research-token-synthetic-not-production",
    tenant_id="11", task_id="test-task", expires_at=2000000000,
    fingerprint="sample",
)
HEADERS = {
    "X-SeeAlpha-Engine-Contract-Version": "2",
    "X-SeeAlpha-Research-Contract-Version": "1",
}


def _client(handler, grant=GRANT):
    return DirectEngineResearchClient(
        "https://tenant-development-engine.internal", grant,
        transport=httpx.MockTransport(handler),
    )


def test_calls_only_fixed_research_endpoints_with_scoped_capability():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        assert request.url.host == "tenant-development-engine.internal"
        assert request.headers["Authorization"] == f"Bearer {GRANT.token}"
        assert request.headers["X-SeeAlpha-Research-Contract-Version"] == "1"
        if request.url.path.endswith("/datasets"):
            return httpx.Response(200, headers=HEADERS, json={"datasets": []})
        if request.url.path.endswith("/snapshots") and request.method == "POST":
            assert b'"dataset_id":"futures_um:ETHUSDT:1m"' in request.content
            return httpx.Response(202, headers=HEADERS, json={"snapshot_id": "snap-1"})
        if request.url.path.endswith("/backtests") and request.method == "POST":
            assert b'"execution":"simulation"' in request.content
            assert b'"purpose":"development"' in request.content
            return httpx.Response(202, headers=HEADERS, json={"backtest_id": "bt-1"})
        return httpx.Response(200, headers=HEADERS, json={"backtest_id": "bt-1"})

    with _client(handler) as client:
        assert client.discover_datasets() == {"datasets": []}
        assert client.create_snapshot("futures_um:ETHUSDT:1m", "2026-01-01", "2026-01-02") == {
            "snapshot_id": "snap-1",
        }
        assert client.submit_backtest({
            "strategy_ref": "account:7",
            "dataset_refs": ["futures_um:ETHUSDT:1m"],
            "start": "2026-01-01",
            "end": "2026-01-02",
            "starting_balance": "1000",
            "quote_currency": "USDT",
            "parameters": {},
            "idempotency_key": "turn-1",
        }) == {"backtest_id": "bt-1"}
        client.get_backtest("bt-1")
        client.cancel_backtest("bt-1")
        with pytest.raises(ValueError):
            client.get_backtest("../admin")

    assert seen == [
        ("GET", "/research/v1/datasets"),
        ("POST", "/research/v1/snapshots"),
        ("POST", "/research/v1/backtests"),
        ("GET", "/research/v1/backtests/bt-1"),
        ("POST", "/research/v1/backtests/bt-1/cancel"),
    ]


@pytest.mark.parametrize("url", [
    "file:///tmp/engine",
    "https://trusted/path",
    "https://trusted?url=http://other",
    "https://attacker@trusted",
    "https://trusted/#fragment",
])
def test_rejects_dynamic_or_malformed_engine_origin(url):
    with pytest.raises(ValueError):
        DirectEngineResearchClient(url, GRANT)


def test_rejects_cross_tenant_or_expired_engine_grant_response_without_leaking_body():
    def handler(_request):
        return httpx.Response(403, content=b"secret-signing-key", headers=HEADERS)

    with _client(handler) as client, pytest.raises(DirectResearchError) as error:
        client.discover_datasets()
    assert "secret-signing-key" not in str(error.value)
    assert GRANT.token not in str(error.value)


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"Location": "http://evil.local/secret"}),
    httpx.Response(200, json={"datasets": []}),
    httpx.Response(200, headers=HEADERS, content=b"x" * 1_048_577),
    httpx.Response(200, headers=HEADERS, content=b"<html>malformed</html>"),
])
def test_fails_closed_for_redirect_version_size_and_non_json(response):
    with _client(lambda _request: response) as client:
        with pytest.raises(DirectResearchError):
            client.discover_datasets()


def test_rejects_arbitrary_code_or_production_backtest_payload_before_network():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers=HEADERS, json={})

    with _client(handler) as client:
        for payload in (
            {"shell": "rm -rf /", "purpose": "production"},
            {"strategy_ref": "evil.py"},
            {"strategy_ref": "account:7", "dataset_refs": [], "parameters": {}},
        ):
            with pytest.raises(ValueError):
                client.submit_backtest(payload)
    assert calls == []


def test_snapshot_integrity_and_limits():
    snapshot_id = "snap-1"
    raw = b"synthetic-parquet-fixture"
    meta = {
        "snapshot_id": snapshot_id,
        "status": "ready",
        "format": "parquet",
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }

    def success(request):
        if request.url.path.endswith("/content"):
            return httpx.Response(200, headers=HEADERS, content=raw)
        return httpx.Response(200, headers=HEADERS, json=meta)

    with _client(success) as client:
        assert client.fetch_snapshot(snapshot_id) == raw

    def tampered(request):
        if request.url.path.endswith("/content"):
            return httpx.Response(200, headers=HEADERS, content=b"x" * len(raw))
        return httpx.Response(200, headers=HEADERS, json=meta)

    with _client(tampered) as client:
        with pytest.raises(DirectResearchError, match="integrity"):
            client.fetch_snapshot(snapshot_id)

    def oversize(_request):
        return httpx.Response(200, headers=HEADERS, json={
            **meta, "size_bytes": 100 * 1_048_576,
        })

    with _client(oversize) as client:
        with pytest.raises(DirectResearchError, match="metadata"):
            client.fetch_snapshot(snapshot_id)
