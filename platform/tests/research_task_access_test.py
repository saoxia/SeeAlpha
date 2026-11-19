"""Scoped development research: durable budgets, real resource gates, no Engine admin bearer."""
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from api.research.direct_engine import DirectEngineResearchClient
from api.research.task_access import ResearchLeases, allowed_scope
from api.research.grants import ResearchGrant
from api.tenant_docker import DockerTenantManager
from core.engine_contracts import EngineConflict


@pytest.fixture
def leases(tmp_path):
    return ResearchLeases(tmp_path / "private" / "research.sqlite3")


def _issue(store, task_id="task-uuid", turn=1):
    return store.issue(
        task_id=task_id, owner_id=7, tenant_id=11,
        account_id="5", dataset_ref="futures_um:ETHUSDT:1m", turn=turn,
    )


def test_task_token_is_hashed_persistent_and_turn_bound(leases):
    token, expires = _issue(leases)
    assert expires > 0
    raw = Path(leases.path).read_bytes()
    assert token.encode() not in raw
    assert Path(leases.path).stat().st_mode & 0o077 == 0
    assert leases.authenticated("task-uuid", token)["owner_id"] == 7
    assert leases.consume("task-uuid", token, 1, "datasets")["remaining"] == 80
    after_restart = ResearchLeases(leases.path)
    assert after_restart.authenticated("task-uuid", token)["turn"] == 1
    replacement, _ = _issue(after_restart, turn=2)
    assert replacement != token
    with pytest.raises(HTTPException) as rejected:
        after_restart.authenticated("task-uuid", token)
    assert rejected.value.status_code == 403
    with pytest.raises(HTTPException):
        after_restart.consume("task-uuid", replacement, 1, "datasets")
    assert after_restart.consume("task-uuid", replacement, 2, "datasets")["turn"] == 2
    after_restart.revoke("task-uuid")
    with pytest.raises(HTTPException):
        after_restart.authenticated("task-uuid", replacement)


def test_rejects_forged_expired_and_unbudgeted_calls(leases):
    token, _ = _issue(leases)
    for wrong in ("a" * 64, token[:-1] + "A"):
        with pytest.raises(HTTPException):
            leases.authenticated("task-uuid", wrong)
    with pytest.raises(HTTPException) as err:
        leases.consume("task-uuid", token, 1, "shell")
    assert err.value.status_code == 404
    with leases._connect() as db:
        db.execute("UPDATE research_leases SET remaining=0 WHERE task_id='task-uuid'")
    with pytest.raises(HTTPException) as err:
        leases.consume("task-uuid", token, 1, "datasets")
    assert err.value.status_code == 429
    with leases._connect() as db:
        db.execute("UPDATE research_leases SET expires_at=1 WHERE task_id='task-uuid'")
    with pytest.raises(HTTPException):
        leases.authenticated("task-uuid", token)


def test_scope_checks_active_owner_account_issuance_policy(monkeypatch):
    from api.research import task_access

    tenant = {"id": 11, "owner_id": 7, "purpose": "development", "status": "active"}
    accounts = [{"id": 5}]
    policy = [{"product": "futures_um", "symbol": "ETHUSDT",
               "parameters": {"history_enabled": True, "intervals": ["1m", "5m"]}}]
    loads = [{"account_id": "5", "purpose": "development",
              "parameters": {"symbol": "ETHUSDT"}}]
    class FakePolicy:
        def __init__(self, _database):
            pass
        def records(self, _tenant_id):
            return policy
    monkeypatch.setattr(task_access, "PolicyRepository", FakePolicy)
    app = SimpleNamespace(state=SimpleNamespace(
        database=object(),
        tenants=SimpleNamespace(repository=SimpleNamespace(
            get_tenant=lambda _id: tenant,
            list_virtual_accounts=lambda _id: accounts,
        )),
        user_resources=SimpleNamespace(enabled=lambda _id, _kind: True),
        strategies=SimpleNamespace(repository=SimpleNamespace(
            list_issuances=lambda _id: loads,
        )),
    ))
    def allowed():
        return allowed_scope(app, 7, 11, "5", "futures_um:ETHUSDT:1m")
    assert allowed() is True
    assert allowed_scope(app, 8, 11, "5", "futures_um:ETHUSDT:1m") is False
    assert allowed_scope(app, 7, 11, "99", "futures_um:ETHUSDT:1m") is False
    assert allowed_scope(app, 7, 11, "5", "futures_um:BTCUSDT:1m") is False
    tenant["purpose"] = "production"
    assert allowed() is False
    tenant["purpose"] = "development"
    policy[0]["parameters"]["history_enabled"] = False
    assert allowed() is False
    policy[0]["parameters"]["history_enabled"] = True
    loads[0]["parameters"]["symbol"] = "BTCUSDT"
    assert allowed() is False


def test_local_docker_research_key_only_on_explicit_new_development(tmp_path):
    def manager(enabled):
        return DockerTenantManager(
            tmp_path / ("enabled" if enabled else "disabled"),
            image="engine:pinned", production_cpus="1", production_memory="1g",
            development_cpus="1", development_memory="1g", log_root=tmp_path,
            research_enabled=enabled,
        )
    enabled = manager(True)
    root, *_other, path = enabled._paths("11")
    root.mkdir(parents=True, exist_ok=True)
    enabled._credentials(path, "11", "development", create=True)
    key = enabled.research_signing_key("11")
    assert len(key) >= 32
    assert key not in enabled._credentials(path, "11", "development", create=False)
    with pytest.raises(EngineConflict):
        enabled.research_signing_key("12")
    disabled = manager(False)
    other, *_other, path2 = disabled._paths("11")
    other.mkdir(parents=True, exist_ok=True)
    disabled._credentials(path2, "11", "development", create=True)
    with pytest.raises(EngineConflict):
        disabled.research_signing_key("11")


def test_direct_client_uses_scoped_grant_only_not_engine_admin_token():
    grant = ResearchGrant(
        token="test-scoped-engine-grant", tenant_id="11", task_id="task-1",
        expires_at=1900000000, fingerprint="digest",
    )
    seen = []
    def handler(request):
        seen.append(request)
        assert request.headers["authorization"] == "Bearer test-scoped-engine-grant"
        assert request.url.path.startswith("/research/v1/")
        return httpx.Response(
            200,
            headers={"X-SeeAlpha-Engine-Contract-Version": "2",
                     "X-SeeAlpha-Research-Contract-Version": "1"},
            json={"datasets": []},
        )
    with DirectEngineResearchClient(
        "https://development-engine.local", grant,
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.discover_datasets() == {"datasets": []}
    assert len(seen) == 1
