"""Venue HTTP queries remain protected by tenant identity and Engine token."""
from decimal import Decimal

from fastapi.testclient import TestClient

from engine.app import create_app
from engine.config import EngineConfig


def test_public_venue_query_tenant_identity_and_contract(tmp_path, monkeypatch):
    from engine.connectors.binance import assets, manual

    monkeypatch.setattr(
        manual, "market_tickers", _ticker,
    )
    monkeypatch.setattr(
        assets, "asset_price", lambda env, currency: {"price": Decimal("1.000000001"), "priced_at": 123},
    )
    app = create_app(EngineConfig(tmp_path / "state", "private", project=tmp_path, tenant_id="23", tenant_purpose="development"))
    with TestClient(app) as client:
        auth = {"Authorization": "Bearer private"}
        assert client.post("/tenants/23/venue/tickers", json={"environment": "production"}).status_code == 401
        assert client.post("/tenants/24/venue/tickers", headers=auth, json={"environment": "production"}).status_code == 403
        assert client.post("/tenants/23/venue/tickers", headers=auth, json={"environment": "production"}).json()["rows"]["ETHUSDT"]["last_price"] == "2000"
        assert client.post(
            "/tenants/23/venue/asset-account", headers=auth,
            json={"environment": "production", "api_key": "fake", "api_secret": "fake", "account_id": "1"},
        ).status_code == 403
        price = client.post("/tenants/23/venue/asset-price", headers=auth, json={
            "environment": "production", "currency": "USDT",
        })
        assert price.status_code == 200
        assert price.json()["price"] == "1.000000001"
        assert client.post("/tenants/23/venue/asset-price", headers=auth, json={
            "environment": "production", "currency": "../etc/passwd",
        }).status_code == 422


async def _ticker(env):
    return {"checked_at": 123, "rows": {"ETHUSDT": {"last_price": "2000"}}}
