"""Disposable Engine cross-process business-boundary acceptance.

Uses the isolated Platform source tree plus a real, temporary Engine Docker
container started by verify_external_engine.sh. No exchange credentials, live
orders, strategies, data downloads or persistent database are involved.

This intentionally provisions ONE development tenant inside a disposable Engine
SQLite volume, unlike the read-only HTTP v2 handshake script.
"""

import ipaddress
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit


def _environment():
    if os.environ.get("SEEALPHA_SMOKE_DISPOSABLE") != "1":
        raise RuntimeError("Refusing tenant provisioning without disposable Engine marker")
    root = Path(os.environ["SEEALPHA_ISOLATED_PLATFORM_ROOT"]).resolve()
    url = os.environ["ENGINE_URL"].rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or not parsed.hostname
            or not ipaddress.ip_address(parsed.hostname).is_loopback
            or parsed.username or parsed.password
            or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise RuntimeError("Business smoke requires a loopback-only Engine HTTP URL")
    token = os.environ["ENGINE_TOKEN"]
    if not token or token == "seealpha-local-engine":
        raise RuntimeError("Refusing to use a shared or default Engine token")
    tenant_id = os.environ["ENGINE_SMOKE_TENANT_ID"]
    if not tenant_id.isdecimal() or int(tenant_id) < 100000000:
        raise RuntimeError("Expected a dedicated high-range disposable tenant id")
    if (root / "engine").exists() or (root / "nautilus_support.py").exists():
        raise RuntimeError("Platform test tree must not contain legacy Engine source")
    return root, url, token, tenant_id


def main():
    root, url, token, tenant_id = _environment()
    # Ignore original monorepo's installed runtime/source; import only copied API/core.
    sys.path.insert(0, str(root))
    from api.engine_client import EngineClient
    from core.engine_contracts import EngineConflict, QueryError

    assert Path(sys.modules["api"].__file__).resolve().is_relative_to(root)
    assert Path(sys.modules["core"].__file__).resolve().is_relative_to(root)
    assert "engine" not in sys.modules
    assert "nautilus_trader" not in sys.modules

    client = EngineClient(root / "state", url=url, token=token)
    try:
        # This is an isolated container and its data/state are destroyed on exit.
        tenant = client.ensure_tenant(tenant_id, "development")
        assert tenant["tenant_id"] == tenant_id
        assert tenant["purpose"] == "development"
        assert tenant["status"] == "active"
        assert client.ensure_tenant(tenant_id, "development") == tenant

        runnable = client.runnable(tenant_id, "backtest")
        assert runnable["tenant_id"] == tenant_id
        assert runnable["status"] == "runnable"

        try:
            client.runnable(tenant_id, "live")
        except EngineConflict:
            pass
        else:
            raise AssertionError("A development tenant was allowed to trade live")

        # Fresh Engine ledger must not contain any synthetic account trading facts.
        for resource in ("orders", "fills", "positions"):
            result = client._sync(
                "GET", f"/tenants/{tenant_id}/accounts/1/{resource}",
                timeout=5, params={"environment": "sandbox"},
            )
            assert result[resource] == [], (resource, result)

        # The fixed-tenant Engine container must reject even read requests for
        # another tenant; never send a command that could create state there.
        try:
            client._sync(
                "GET", f"/tenants/{int(tenant_id) + 1}/accounts/1/orders",
                timeout=5, params={"environment": "sandbox"},
            )
        except QueryError:
            pass
        else:
            raise AssertionError("Engine returned another tenant's order ledger")
        assert "engine" not in sys.modules
        assert "nautilus_trader" not in sys.modules
    finally:
        client.close()
    print("PASS: independent Platform EngineClient tenant lifecycle, dev-only gate, empty ledgers, tenant isolation")


if __name__ == "__main__":
    main()
