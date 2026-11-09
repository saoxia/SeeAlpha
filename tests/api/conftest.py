"""Legacy in-process Engine fixture for combined business API regressions.

This suite still uses the old Engine implementation for tests that inspect
Engine internals or inject Catalog/data fixtures. Do not reuse this fixture in
standalone Platform tests. The independent cross-process checks belong to
platform/tests and scripts/verify_external_engine*.py.

Engine imports are intentionally delayed until a legacy test runs, rather than
being loaded while pytest collects Platform-related test modules.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from api.engine_client import EngineClient


@pytest.fixture(autouse=True)
def engine_http(tmp_path, monkeypatch):
    from engine.app import create_app as create_engine_app
    from engine.config import EngineConfig

    engine_app = create_engine_app(
        EngineConfig(
            tmp_path / "engine", token="seealpha-local-engine",
            project=tmp_path,
        )
    )
    with TestClient(engine_app, raise_server_exceptions=False) as http:
        http.headers["Authorization"] = "Bearer seealpha-local-engine"

        def forward(request):
            target = request.url.raw_path.decode("ascii")
            result = http.request(
                request.method, target, content=request.content,
                headers=request.headers,
            )
            return httpx.Response(
                result.status_code, headers=result.headers, content=result.content,
            )

        sync_transport = httpx.MockTransport(forward)
        async_transport = httpx.ASGITransport(app=engine_app, raise_app_exceptions=False)

        class HTTPOnlyTestEngineClient(EngineClient):
            def __init__(self, *args, **kwargs):
                kwargs.setdefault("sync_transport", sync_transport)
                kwargs.setdefault("async_transport", async_transport)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(api_main, "EngineClient", HTTPOnlyTestEngineClient)
        yield http
    engine_app.state.runtime.close()
