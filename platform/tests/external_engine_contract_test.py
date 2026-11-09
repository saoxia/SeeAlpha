"""Read-only HTTP v2 smoke from Engine-free Platform tests.

Opt-in only; use a loopback Engine with isolated test state and a disposable token.
No state mutation, orders, exchange credentials, or tenant provisioning.
"""

import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from api.engine_client import EngineClient
from core.engine_contracts import ENGINE_CONTRACT_HEADER, ENGINE_CONTRACT_VERSION

PROJECT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.environ.get("SEEALPHA_TEST_EXTERNAL_ENGINE") != "1",
    reason="set SEEALPHA_TEST_EXTERNAL_ENGINE=1 for opt-in isolated Engine HTTP smoke",
)


def test_external_engine_is_reachable_with_platform_http_client():
    url = os.environ.get("ENGINE_URL", "").rstrip("/")
    token = os.environ.get("ENGINE_TOKEN", "")
    parsed = urlsplit(url)
    assert parsed.scheme == "http" and parsed.hostname, "Use a loopback HTTP Engine"
    assert ipaddress.ip_address(parsed.hostname).is_loopback, "Never probe a remote Engine"
    assert not parsed.username and not parsed.password and not parsed.query
    assert not parsed.fragment and parsed.path in {"", "/"}
    assert token and token != "seealpha-local-engine", "Use a disposable private token"

    client = EngineClient(PROJECT, url=url, token=token)
    health = client._sync("GET", "/health", timeout=5)
    assert health["contract_version"] == ENGINE_CONTRACT_VERSION

    with httpx.Client(timeout=5, trust_env=False) as http:
        unauthenticated = http.get(url + "/health")
        assert unauthenticated.status_code == 401
        incompatible = http.get(
            url + "/health",
            headers={
                "Authorization": f"Bearer {token}",
                ENGINE_CONTRACT_HEADER: "incompatible",
            },
        )
        assert incompatible.status_code == 426
        assert incompatible.headers[ENGINE_CONTRACT_HEADER] == ENGINE_CONTRACT_VERSION
