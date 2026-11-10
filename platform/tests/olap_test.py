import pytest
from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import AuthRepository
from api.db.database import Database
from api.main import create_app
from fastapi.testclient import TestClient


# Run the actual Platform lifespan with an Engine-free HTTP bootstrap stub.
pytestmark = pytest.mark.usefixtures("platform_engine_bootstrap")

def test_olap_settings_and_monitor_persist_and_require_auth(tmp_path):
    settings = Settings(tmp_path / "auth.db", cookie_secure=False)
    db = Database(settings.database_path)
    db.initialize()
    AuthRepository(db).create_user("admin", hash_password("test-password-123"))
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/system/olap").status_code == 401
        assert client.get("/api/clickhouse/monitor/status").status_code == 401
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin", "password": "test-password-123"}
            ).status_code
            == 200
        )
        payload = {
            "address": "localhost",
            "port": 8123,
            "database": "default",
            "username": "user",
            "password": "secret",
        }
        result = client.put("/api/system/olap", json=payload)
        assert result.status_code == 200
        assert result.json()["password_configured"] is True
        assert "secret" not in result.text
        payload["password"] = None
        assert client.put("/api/system/olap", json=payload).status_code == 200
        assert app.state.olap.private_olap_settings()["password"] == "secret"
        app.state.olap.available_tables = lambda: [{"name": "ticks", "time_column": "timestamp"}]
        app.state.olap.execute_json = lambda *args: [{"latest_epoch": 1}]
        assert client.post("/api/system/olap/test").json()["table_count"] == 1
        response = client.post("/api/clickhouse/monitor/tables", json={"table": "ticks"})
        assert response.status_code == 201
        assert response.json()["items"][0]["table"] == "ticks"
        assert client.get("/api/clickhouse/monitor/status").json()["tables"] == ["ticks"]
        assert client.delete("/api/clickhouse/monitor/tables/ticks").json()["tables"] == []
        assert (
            client.post("/api/clickhouse/monitor/tables", json={"table": "bad;sql"}).status_code
            == 422
        )
    with TestClient(create_app(settings)) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "test-password-123"})
        assert client.get("/api/system/olap").json()["password_configured"]
