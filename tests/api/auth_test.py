"""Authentication, revocation, browser boundaries and session persistence."""

import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from api.auth.manage import set_user_password
from api.auth.security import hash_password, token_digest, verify_password
from api.config import Settings
from api.db.auth_repository import AuthRepository
from api.db.database import Database
from api.main import create_app


@pytest.fixture
def environment(tmp_path):
    settings = Settings(tmp_path / "auth.db", cookie_secure=False)
    database = Database(settings.database_path)
    database.initialize()
    repository = AuthRepository(database)
    repository.create_user("admin", hash_password("test-password-123"))
    return settings, database, repository


def login(client, password="test-password-123", username="admin"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_login_refresh_logout_and_replay(environment):
    settings, database, _ = environment
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/auth/me").status_code == 401
        assert login(client, password="wrong").status_code == 401
        response = login(client, username="ADMIN")
        assert response.status_code == 200
        assert response.json()["user"]["username"] == "admin"
        cookie = client.cookies.get("seealpha_session")
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "SameSite=strict" in response.headers["set-cookie"]
        assert response.headers["cache-control"] == "no-store"
        with database.connect() as connection:
            stored = connection.execute("SELECT token_hash FROM sessions").fetchone()[0]
        assert stored == token_digest(cookie) and stored != cookie
        # A fresh app instance reads the persisted session, as a refresh/restart would.
        with TestClient(create_app(settings)) as refreshed:
            refreshed.cookies.set("seealpha_session", cookie)
            assert refreshed.get("/api/auth/me").status_code == 200
        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/auth/me").status_code == 401
        client.cookies.set("seealpha_session", cookie)
        assert client.get("/api/auth/me").status_code == 401


def test_expired_session_and_password_reset(environment):
    settings, database, repository = environment
    with TestClient(create_app(settings)) as client:
        login(client)
        with database.connect() as connection:
            connection.execute("UPDATE sessions SET expires_at = ?", (int(time.time()) - 1,))
        assert client.get("/api/auth/me").status_code == 401
        login(client)
        set_user_password(database, "admin", "new-password-123", create=False)
        assert client.get("/api/auth/me").status_code == 401
        assert login(client).status_code == 401
        assert login(client, "new-password-123").status_code == 200
        assert verify_password(
            repository.get_user_by_username("admin")["password_hash"], "new-password-123"
        )


def test_rate_limit_cannot_be_evaded_with_forwarded_header(environment):
    settings, _, _ = environment
    with TestClient(create_app(settings)) as client:
        for number in range(5):
            response = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "wrong"},
                headers={"x-forwarded-for": f"192.0.2.{number}"},
            )
            assert response.status_code == 401
        response = login(client)
        assert response.status_code == 429
        assert response.headers["cache-control"] == "no-store"


def test_origin_host_and_secure_cookie(environment):
    settings, _, _ = environment
    settings = replace(settings, cookie_secure=True, allowed_origins=("https://frontend.example",))
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        for origin in ("https://evil.example", "null"):
            response = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "test-password-123"},
                headers={"origin": origin},
            )
            assert response.status_code == 403
        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "test-password-123"},
            headers={"origin": "https://frontend.example"},
        )
        assert response.status_code == 200
        assert "Secure" in response.headers["set-cookie"]
        assert (
            client.post("/api/auth/logout", headers={"origin": "https://evil.example"}).status_code
            == 403
        )
        assert client.get("/api/auth/me").status_code == 200
        assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400


def test_password_whitespace_is_preserved_and_no_public_registration(environment):
    settings, database, _ = environment
    set_user_password(database, "new-user", "  long-password  ", create=True)
    with TestClient(create_app(settings)) as client:
        assert login(client, "long-password", "new-user").status_code == 401
        assert login(client, "  long-password  ", "new-user").status_code == 200
        assert client.post("/api/auth/register", json={}).status_code == 404


def test_session_lookup_does_not_write_and_still_enforces_expiry_and_revocation(environment):
    _settings, database, repository = environment
    user = repository.get_user_by_username("admin")
    now = int(time.time())
    repository.create_session(user["id"], "valid-read-only", now + 60)
    repository.create_session(user["id"], "expired-read-only", now - 1)
    assert repository.get_user_for_session("valid-read-only")["id"] == user["id"]
    assert repository.get_user_for_session("expired-read-only") is None
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM sessions WHERE token_hash='expired-read-only'").fetchone()[0] == 1
    repository.delete_session("valid-read-only")
    assert repository.get_user_for_session("valid-read-only") is None
    repository.create_session(user["id"], "next-login", now + 60)
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM sessions WHERE token_hash='expired-read-only'").fetchone()[0] == 0
