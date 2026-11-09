"""Mock trusted-Gateway exchange; no real network or issued Engine token."""
import json

import httpx
import pytest

from api.research.gateway_client import (
    GATEWAY_HEADER,
    GatewayControlClient,
    GatewayRegistrationError,
)
from api.research.grants import ResearchGrant

TOKEN = "platform-only-gateway-control-secret-xxxxxx"
GRANT = ResearchGrant(
    token="signed-task-research-grant-secret",
    tenant_id="42", task_id="research-001",
    expires_at=1700000600, fingerprint="abc123",
)
BRIDGE = "bridge-one-time-capability-xxxxxxxxxxxxxxxxx"


def test_register_and_revoke_are_private_server_to_server():
    seen = []
    def mock_gateway(request):
        seen.append(request)
        assert request.url.host == "trusted-gateway"
        assert request.headers["authorization"] == "Bearer " + TOKEN
        assert request.headers[GATEWAY_HEADER] == "1"
        if request.method == "POST":
            assert request.url.path == "/internal/v1/leases"
            body = json.loads(request.content)
            assert body == {
                "tenant_id": GRANT.tenant_id, "task_id": GRANT.task_id,
                "engine_grant": GRANT.token, "max_calls": 10,
            }
            return httpx.Response(201, headers={GATEWAY_HEADER: "1"}, json={
                "tenant_id": GRANT.tenant_id, "task_id": GRANT.task_id,
                "expires_at": GRANT.expires_at, "bridge_token": BRIDGE,
                "remaining_calls": 10,
            })
        assert request.method == "DELETE"
        assert request.url.path == "/internal/v1/tasks/research-001"
        return httpx.Response(204, headers={GATEWAY_HEADER: "1"})
    with GatewayControlClient(
        "http://trusted-gateway", TOKEN,
        transport=httpx.MockTransport(mock_gateway),
    ) as client:
        registration = client.register(GRANT, max_calls=10)
        assert registration.bridge_token == BRIDGE
        assert "bridge_token" not in registration.public()
        assert GRANT.token not in repr(registration)
        assert BRIDGE not in repr(registration)
        client.revoke(GRANT.task_id)
    assert len(seen) == 2


@pytest.mark.parametrize("bad", [
    "http://trusted-gateway/other",
    "https://user:pass@trusted-gateway",
    "http://trusted-gateway?token=foo",
    "file:///tmp/local",
])
def test_rejects_untrusted_gateway_origins(bad):
    with pytest.raises(ValueError):
        GatewayControlClient(bad, TOKEN)


def test_rejects_missing_headers_and_inconsistent_lease():
    def mismatch(_request):
        return httpx.Response(201, headers={GATEWAY_HEADER: "1"}, json={
            "tenant_id": "other", "task_id": "research-001",
            "expires_at": GRANT.expires_at, "bridge_token": BRIDGE,
        })
    with GatewayControlClient(
        "http://trusted-gateway", TOKEN,
        transport=httpx.MockTransport(mismatch),
    ) as client:
        with pytest.raises(GatewayRegistrationError, match="inconsistent"):
            client.register(GRANT)
    def no_version(_request):
        return httpx.Response(201, json={})
    with GatewayControlClient(
        "http://trusted-gateway", TOKEN,
        transport=httpx.MockTransport(no_version),
    ) as client:
        with pytest.raises(GatewayRegistrationError, match="contract mismatch"):
            client.register(GRANT)


def test_never_echoes_gateway_error_bodies_or_secrets():
    def denied(_request):
        return httpx.Response(
            403, json={"detail": "Engine grant=" + GRANT.token},
        )
    with GatewayControlClient(
        "http://trusted-gateway", TOKEN,
        transport=httpx.MockTransport(denied),
    ) as client:
        with pytest.raises(GatewayRegistrationError) as exc:
            client.register(GRANT)
        assert GRANT.token not in str(exc.value)
        assert TOKEN not in str(exc.value)
    def network_failure(_request):
        raise httpx.ConnectError("secret " + TOKEN)
    with GatewayControlClient(
        "http://trusted-gateway", TOKEN,
        transport=httpx.MockTransport(network_failure),
    ) as client:
        with pytest.raises(GatewayRegistrationError) as exc:
            client.register(GRANT)
        assert TOKEN not in str(exc.value)
