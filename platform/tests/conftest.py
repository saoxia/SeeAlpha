"""Strict, opt-in Engine HTTP bootstrap stub for Platform-owned API tests.

Only startup tenant registration and the empty market-job/policy reconciliation
performed by a fresh Platform app are emulated here. Anything else must be tested
against a real external Engine or
explicitly mocked in that specific test. This fixture never starts Docker,
imports Engine, or forwards a request to a network endpoint.
"""

import json
import re

import api.main as api_main
import httpx
import pytest
from api.engine_client import EngineClient
from core.engine_contracts import ENGINE_CONTRACT_HEADER, ENGINE_CONTRACT_VERSION


@pytest.fixture
def platform_engine_bootstrap(monkeypatch):
    recorded = []

    def respond(request):
        assert request.url.host == "127.0.0.1"
        assert request.headers.get(ENGINE_CONTRACT_HEADER) == ENGINE_CONTRACT_VERSION
        assert request.headers.get("authorization") == "Bearer seealpha-local-engine"
        if request.method == "PUT" and re.fullmatch(r"/tenants/[1-9][0-9]*", request.url.path):
            payload = json.loads(request.content)
            assert payload["purpose"] in {"development", "production"}
            assert payload["status"] in {"active", "archived"}
            tenant = request.url.path.rsplit("/", 1)[-1]
            recorded.append((tenant, payload["purpose"], payload["status"]))
            body = {
                "tenant_id": tenant, "purpose": payload["purpose"],
                "status": payload["status"],
            }
        elif request.method == "POST" and request.url.path == "/tenants/1/data/control/jobs":
            assert json.loads(request.content) == {}
            recorded.append(("market-reconcile", "jobs"))
            body = []
        elif request.method == "POST" and request.url.path == "/tenants/1/data/control/sync":
            assert json.loads(request.content) == {"policies": []}
            recorded.append(("market-restore", "empty-policies"))
            body = {"last_error": None}
        else:
            raise AssertionError(
                "Unexpected Engine call in Platform-owned test: "
                f"{request.method} {request.url.path}"
            )
        return httpx.Response(
            200, json=body, headers={ENGINE_CONTRACT_HEADER: ENGINE_CONTRACT_VERSION},
        )

    transport = httpx.MockTransport(respond)

    class PlatformBootstrappedEngineClient(EngineClient):
        def __init__(self, *args, **kwargs):
            if kwargs.get("tenant_manager") is not None:
                raise AssertionError("Platform-only bootstrap must not create Docker")
            kwargs["sync_transport"] = transport
            kwargs["async_transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(api_main, "EngineClient", PlatformBootstrappedEngineClient)
    yield recorded
