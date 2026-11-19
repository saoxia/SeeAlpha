"""Platform research HTTP control denies forged, stale and terminated capabilities."""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.research import task_router
from api.research.task_access import ResearchLeases


def test_platform_task_commands_require_bound_live_capability(tmp_path, monkeypatch):
    task_id = str(uuid4())
    leases = ResearchLeases(tmp_path / "research" / "leases.db")
    token, _expiry = leases.issue(
        task_id=task_id, owner_id=7, tenant_id=11,
        account_id="5", dataset_ref="futures_um:ETHUSDT:1m", turn=1,
    )
    app = FastAPI()
    app.state.research_leases = leases
    app.state.repository = SimpleNamespace(
        get_user_by_id=lambda owner_id: {"username": "research-owner"}
        if owner_id == 7 else None
    )
    app.state.settings = SimpleNamespace()
    app.include_router(task_router.router, prefix="/api")
    observed = {"status": "running", "turn": 1, "called": 0}

    @contextmanager
    def harness(_request, _actor):
        class Fake:
            def get(self, _task_id):
                return {"status": observed["status"], "turn": observed["turn"]}
        yield Fake()

    def command(_app, lease, operation, arguments):
        observed["called"] += 1
        assert lease["owner_id"] == 7
        assert lease["tenant_id"] == 11
        assert lease["account_id"] == "5"
        assert operation == "datasets"
        assert arguments == {}
        return {"datasets": []}

    monkeypatch.setattr(task_router, "_client", harness)
    monkeypatch.setattr(task_router, "_require_harness_owner", lambda *_args: None)
    monkeypatch.setattr(task_router, "research_command", command)
    endpoint = f"/api/research/tasks/{task_id}/commands/datasets"
    with TestClient(app) as client:
        assert client.post(endpoint, json={"arguments": {}}).status_code == 401
        assert client.post(
            endpoint, headers={"Authorization": "Bearer " + "q" * 64},
            json={"arguments": {}},
        ).status_code == 403
        assert observed["called"] == 0
        ok = client.post(
            endpoint, headers={"Authorization": "Bearer " + token},
            json={"arguments": {}},
        )
        assert ok.status_code == 200
        assert ok.json() == {"datasets": []}
        assert observed["called"] == 1
        observed["turn"] = 2
        assert client.post(
            endpoint, headers={"Authorization": "Bearer " + token},
            json={"arguments": {}},
        ).status_code == 403
        observed["turn"] = 1
        observed["status"] = "cancelled"
        assert client.post(
            endpoint, headers={"Authorization": "Bearer " + token},
            json={"arguments": {}},
        ).status_code == 409
        observed["status"] = "running"
        leases.revoke(task_id)
        assert client.post(
            endpoint, headers={"Authorization": "Bearer " + token},
            json={"arguments": {}},
        ).status_code == 403
