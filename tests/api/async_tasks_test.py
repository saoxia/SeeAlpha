"""Business task endpoints require session auth and forward through EngineClient."""
from fastapi.testclient import TestClient

from tests.api.auth_test import login
from tests.api.backtest_test import _client


def test_admin_async_backtest_and_task_routes_forward_without_running_locally(tmp_path, monkeypatch):
    app = _client(tmp_path)
    service = app.state.strategies
    events = []
    identity = "b" * 32

    def submit(tenant_id, account_id, start, end, interval, *, idempotency_key):
        events.append(("submit", tenant_id, account_id, idempotency_key))
        return {"id": identity, "kind": "backtest", "status": "PENDING"}

    monkeypatch.setattr(service, "submit_backtest", submit)
    monkeypatch.setattr(service, "tasks", lambda tenant_id: [{"id": identity, "status": "PENDING"}])
    monkeypatch.setattr(service, "task", lambda tenant_id, job_id: {
        "id": job_id, "tenant_id": str(tenant_id), "status": "SUCCEEDED",
    })
    monkeypatch.setattr(service, "task_result", lambda tenant_id, job_id: {
        "job_id": job_id, "ending_balance": "1050",
    })
    monkeypatch.setattr(service, "cancel_task", lambda tenant_id, job_id: {
        "id": job_id, "status": "CANCELLED",
    })
    monkeypatch.setattr(service, "retry_task", lambda tenant_id, job_id: {
        "id": "c" * 32, "status": "PENDING", "retry_of": job_id,
    })

    submission = {
        "start": "2024-01-01", "end": "2024-01-02", "interval": "1m",
        "idempotency_key": "study-1",
    }
    with TestClient(app) as http:
        assert http.post(
            "/api/tenants/1/accounts/1/backtests/async", json=submission,
        ).status_code == 401
        login(http)
        created = http.post(
            "/api/tenants/1/accounts/1/backtests/async", json=submission,
        )
        assert created.status_code == 202, created.text
        assert created.json()["id"] == identity
        assert events == [("submit", 1, 1, "study-1")]
        listing = http.get("/api/tenants/1/jobs")
        assert listing.status_code == 200 and listing.json()["items"][0]["id"] == identity
        assert http.get(f"/api/tenants/1/jobs/{identity}").json()["status"] == "SUCCEEDED"
        assert http.get(f"/api/tenants/1/jobs/{identity}/result").json()["ending_balance"] == "1050"
        assert http.post(f"/api/tenants/1/jobs/{identity}/cancel").json()["status"] == "CANCELLED"
        retry = http.post(f"/api/tenants/1/jobs/{identity}/retry")
        assert retry.status_code == 202
        assert retry.json()["retry_of"] == identity
