"""External Engine HTTP smoke, importing only api/ and core/ from a temporary tree.

Invoked by verify_external_engine.sh with a disposable Engine container and no
production credentials or persistent tenant volumes. This only issues GETs.
"""

import ipaddress
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def main():
    root = Path(os.environ["SEEALPHA_ISOLATED_PLATFORM_ROOT"]).resolve()
    base_url = os.environ["ENGINE_URL"].rstrip("/")
    token = os.environ["ENGINE_TOKEN"]
    tenant_id = os.environ["ENGINE_SMOKE_TENANT_ID"]
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or not ipaddress.ip_address(parsed.hostname).is_loopback
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Only a loopback HTTP Engine may be tested")
    if not token or token == "seealpha-local-engine":
        raise ValueError("Use a disposable Engine token, not the old development default")
    if (root / "engine").exists() or (root / "nautilus_support.py").exists():
        raise AssertionError("The isolated Platform must not contain Engine source")

    # Import the real Platform implementation, never the monorepo source tree.
    sys.path.insert(0, str(root))
    from api.config import Settings
    from api.engine_client import EngineClient
    from api.main import create_app
    from core.engine_contracts import ENGINE_CONTRACT_HEADER, ENGINE_CONTRACT_VERSION, QueryError

    app = create_app(
        Settings(root / "unused.db", database_url="sqlite:///" + str(root / "business.db"))
    )
    assert app.title
    assert Path(sys.modules["api"].__file__).resolve().is_relative_to(root)
    assert Path(sys.modules["core"].__file__).resolve().is_relative_to(root)
    assert "engine" not in sys.modules
    assert "nautilus_trader" not in sys.modules

    headers = {"Authorization": f"Bearer {token}", ENGINE_CONTRACT_HEADER: ENGINE_CONTRACT_VERSION}
    with httpx.Client(timeout=3, trust_env=False) as http:
        # Only HTTP GET; no tenant creation, mutable configuration or orders.
        response = None
        for _ in range(60):
            try:
                response = http.get(base_url + "/health", headers=headers)
                if response.status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(1)
        if response is None or response.status_code != 200:
            raise RuntimeError("Standalone Engine failed to respond with HTTP 200 /health")

        assert response.headers[ENGINE_CONTRACT_HEADER] == ENGINE_CONTRACT_VERSION
        assert response.json()["contract_version"] == ENGINE_CONTRACT_VERSION
        assert http.get(base_url + "/health").status_code == 401
        assert http.get(
            base_url + "/health",
            headers={"Authorization": f"Bearer {token}-invalid"},
        ).status_code == 401
        incompatible = http.get(
            base_url + "/health",
            headers={**headers, ENGINE_CONTRACT_HEADER: "incompatible"},
        )
        assert incompatible.status_code == 426
        assert incompatible.headers[ENGINE_CONTRACT_HEADER] == ENGINE_CONTRACT_VERSION

        # The bound Engine must not expose its own tenant's runtime to others.
        denied = http.get(
            base_url + f"/tenants/{int(tenant_id) + 1}/jobs", headers=headers
        )
        assert denied.status_code == 403

    # The real Platform EngineClient must consume the new Engine v2 response.
    platform_client = EngineClient(root, url=base_url, token=token)
    assert platform_client._sync("GET", "/health", timeout=5)["contract_version"] == "2"
    try:
        platform_client._sync("GET", f"/tenants/{int(tenant_id) + 1}/jobs", timeout=5)
    except QueryError:
        pass
    else:
        raise AssertionError("Platform EngineClient accepted another tenant's Engine")
    assert "engine" not in sys.modules
    assert "nautilus_trader" not in sys.modules
    print("PASS: isolated Platform ASGI import; HTTP v2; 401/403/426; EngineClient")


if __name__ == "__main__":
    main()
