"""Multi-user strategy catalog, sharing and issuer authorization without a live Engine."""

import base64
import hashlib
import io
import json
import zipfile

import pytest
from alembic import command
from alembic.config import Config
from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import auth_repository
from api.main import create_app
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("platform_engine_bootstrap")


def _archive(name="private_signal", version="1"):
    metadata = {
        "format": 1, "name": name, "title": name, "version": version,
        "entry_point": "strategy:Example", "description": "Private strategy",
        "parameters": {"symbol": "ETHUSDT", "allocation": "1"},
    }
    with io.BytesIO() as memory:
        with zipfile.ZipFile(memory, "w") as package:
            package.writestr("seealpha-package.json", json.dumps(metadata))
            package.writestr("strategy.py", "def prepare(parameters): return parameters\\n")
        return base64.b64encode(memory.getvalue()).decode()


def _inspect(archive, parameters, *, tenant_id=None):
    raw = base64.b64decode(archive)
    with zipfile.ZipFile(io.BytesIO(raw)) as package:
        metadata = json.loads(package.read("seealpha-package.json"))
    metadata["digest"] = hashlib.sha256(raw).hexdigest()
    return {"metadata": metadata, "parameters": {**metadata.get("parameters", {}), **parameters}}


def _login(client, username):
    response = client.post("/api/auth/login", json={
        "username": username, "password": "test-password-123",
    })
    assert response.status_code == 200, response.text


@pytest.fixture(params=["standalone", "orm"])
def runtime(request, tmp_path, monkeypatch):
    url = ""
    if request.param == "orm":
        url = f"sqlite:///{tmp_path / 'business.db'}"
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
    settings = Settings(tmp_path / "api.db", database_url=url, cookie_secure=False)
    app = create_app(settings)
    app.state.database.initialize()
    auth = auth_repository(app.state.database)
    for username in ("admin", "alice", "bob"):
        auth.create_user(username, hash_password("test-password-123"))
    monkeypatch.setattr(app.state.engine, "inspect_strategy", _inspect)
    with TestClient(app) as client:
        yield client, auth


def test_system_demo_is_seeded_and_public_for_every_login(runtime):
    client, _ = runtime
    for username in ("alice", "bob"):
        _login(client, username)
        response = client.get("/api/strategy-packages")
        assert response.status_code == 200
        rows = response.json()
        demos = {item["code_key"]: item for item in rows if item["visibility"] == "system"}
        assert set(demos) == {
            "demo_ema_crossover", "demo_funding_carry", "demo_option_hedged_bollinger",
        }
        assert demos["demo_ema_crossover"]["versions"][0]["grants"] == ["development"]
        assert demos["demo_funding_carry"]["versions"][0]["grants"] == []
        assert all(item["owner_id"] is None for item in demos.values())
    _login(client, "alice")
    seed = client.get("/api/strategy-packages").json()[0]
    version = seed["versions"][0]["id"]
    assert client.get(f"/api/strategy-packages/{seed['id']}/versions/{version}/archive").status_code == 200


def test_private_packages_visibility_sharing_clone_and_tenant_gate(runtime):
    client, auth = runtime
    alice = auth.get_user_by_username("alice")["id"]
    bob = auth.get_user_by_username("bob")["id"]
    _login(client, "alice")
    created = client.post("/api/strategy-packages", json={"archive": _archive()})
    assert created.status_code == 201, created.text
    package = created.json()
    assert package["visibility"] == "private"
    assert package["owner_id"] == alice
    pid, vid = package["id"], package["version"]["id"]
    assert client.get("/api/strategy-packages").json()[-1]["code_key"] == "private_signal"
    assert client.get(f"/api/strategy-packages/{pid}/versions/{vid}/archive").status_code == 200

    _login(client, "bob")
    assert pid not in {p["id"] for p in client.get("/api/strategy-packages").json()}
    assert client.post(f"/api/strategy-packages/{pid}/clone", json={}).status_code == 404
    assert client.put(f"/api/strategy-packages/{pid}/visibility",
                      json={"visibility": "public"}).status_code == 404
    assert client.get(f"/api/strategy-packages/{pid}/versions/{vid}/archive").status_code == 404
    assert client.post(f"/api/strategy-packages/{pid}/versions",
                       json={"archive": _archive(version="2")}).status_code == 404

    development = client.post("/api/tenants", json={"name": "Bob private permission test"})
    assert development.status_code == 201, development.text
    tenant_id = development.json()["id"]
    account = client.post(f"/api/tenants/{tenant_id}/accounts", json={
        "account_name": "paper", "initial_capital": "1000", "quote_currency": "USDT",
    })
    assert account.status_code == 201, account.text
    denied = client.post(f"/api/tenants/{tenant_id}/strategy-loads", json={
        "account_id": account.json()["id"], "version_id": vid,
    })
    assert denied.status_code == 404

    _login(client, "alice")
    assert client.post(f"/api/strategy-packages/{pid}/shares",
                       json={"username": "bob"}).status_code == 201
    _login(client, "bob")
    assert pid in {p["id"] for p in client.get("/api/strategy-packages").json()}
    copied = client.post(f"/api/strategy-packages/{pid}/clone", json={})
    assert copied.status_code == 201, copied.text
    personal = copied.json()
    assert personal["owner_id"] == bob
    assert personal["visibility"] == "private"
    assert personal["code_key"] != package["code_key"]
    new_id = personal["id"]
    version = personal["version"]["id"]
    download = client.get(f"/api/strategy-packages/{new_id}/versions/{version}/archive")
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        renamed = json.loads(archive.read("seealpha-package.json"))
    assert renamed["name"] == personal["code_key"]
    assert client.get(f"/api/strategy-packages/{pid}/versions/{vid}/archive").status_code == 404
    assert client.post(f"/api/strategy-packages/{pid}/shares",
                       json={"username": "alice"}).status_code == 404

    _login(client, "alice")
    assert client.delete(f"/api/strategy-packages/{pid}/shares/{bob}").status_code == 200
    _login(client, "bob")
    assert pid not in {p["id"] for p in client.get("/api/strategy-packages").json()}
    _login(client, "alice")
    assert client.put(f"/api/strategy-packages/{pid}/visibility",
                      json={"visibility": "public"}).status_code == 200
    _login(client, "bob")
    assert pid in {p["id"] for p in client.get("/api/strategy-packages").json()}
    assert client.get(f"/api/strategy-packages/{pid}/versions/{vid}/archive").status_code == 404
    _login(client, "alice")
    assert client.put(f"/api/strategy-packages/{pid}/visibility",
                      json={"visibility": "private"}).status_code == 200
    _login(client, "bob")
    assert pid not in {p["id"] for p in client.get("/api/strategy-packages").json()}


def test_same_code_key_allowed_across_owners_and_only_admin_can_grant_production(runtime):
    client, _ = runtime
    _login(client, "alice")
    first = client.post("/api/strategy-packages", json={"archive": _archive()})
    assert first.status_code == 201
    one = first.json()
    assert client.post(f"/api/strategy-packages/versions/{one['version']['id']}/grants",
                       json={"purpose": "production"}).status_code == 403

    _login(client, "bob")
    second = client.post("/api/strategy-packages", json={"archive": _archive()})
    assert second.status_code == 201, second.text
    assert first.json()["id"] != second.json()["id"]
    assert second.json()["version"]["id"] != one["version"]["id"]
    assert client.post("/api/strategy-packages", json={"archive": _archive()}).status_code == 409

    _login(client, "admin")
    assert client.get("/api/strategy-packages").status_code == 200
    assert len([p for p in client.get("/api/strategy-packages").json()
                if p["code_key"] == "private_signal"]) == 2
