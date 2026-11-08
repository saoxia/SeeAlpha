"""Resident engine facts, account scope, and the API process boundary."""

import ast
from pathlib import Path

from fastapi.testclient import TestClient

from engine.app import create_app
from engine.config import EngineConfig
from engine.runtime import numeric_id

PROJECT = Path(__file__).resolve().parents[2]


def test_engine_keeps_per_account_orders_gaps_and_no_secrets(tmp_path, monkeypatch):
    calls = []

    async def execute(account, symbol, command):
        calls.append((account["id"], symbol, command["action"]))
        order = {"client_order_id": command["client_order_id"], "status": "accepted", "symbol": symbol}
        return {"result": order, "records": [{"trade_id": "fill-" + command["client_order_id"], "symbol": symbol}]}

    monkeypatch.setattr("engine.connectors.binance.manual.execute", execute)
    app = create_app(EngineConfig(tmp_path / "state", token="engine-test", project=tmp_path))
    headers = {"Authorization": "Bearer engine-test"}
    order = {
        "environment": "production", "api_key": "key-one", "api_secret": "secret-one",
        "action": "submit", "fingerprint": "a" * 64, "client_order_id": "SA-1",
        "values": {"request_id": "req-1", "symbol": "ETHUSDT-PERP", "account_revision": "f" * 64},
    }
    with TestClient(app) as client:
        assert client.get("/health").status_code == 401
        assert client.get("/health", headers=headers).json()["status"] == "ok"
        first = client.post("/accounts/11/orders", headers=headers, json=order)
        assert first.status_code == 200, first.text
        assert first.json()["response"]["status"] == "completed"
        again = client.post("/accounts/11/orders", headers=headers, json=order)
        assert again.json()["response"] == first.json()["response"]
        assert calls == [(11, "ETHUSDT-PERP", "submit")]
        other = {**order, "api_key": "key-two", "api_secret": "secret-two", "fingerprint": "b" * 64,
                 "client_order_id": "SA-2", "values": {**order["values"], "request_id": "req-2"}}
        assert client.post("/accounts/22/orders", headers=headers, json=other).status_code == 200
        assert [row["client_order_id"] for row in client.get("/accounts/11/orders", headers=headers, params={"environment": "production"}).json()["orders"]] == ["SA-1"]
        assert client.get("/accounts/22/fills", headers=headers, params={"environment": "production"}).json()["fills"][0]["trade_id"] == "fill-SA-2"
        gap = client.post("/accounts/11/gaps", headers=headers, json={
            "scope": "ETHUSDT", "gap_key": "2026-11-08", "gap": {"kind": "missing", "symbol": "ETHUSDT"},
        })
        assert gap.status_code == 200, gap.text
        assert client.get("/accounts/22/gaps", headers=headers).json()["gaps"] == []
        assert client.get("/accounts/11/gaps", headers=headers).json()["gaps"][0]["symbol"] == "ETHUSDT"
        noted = client.post("/accounts/11/market/workers/note", headers=headers, json={
            "command": [".venv/bin/python", "-m", "engine.feed.managed", "--symbol", "ETHUSDT", "--role", "collection"],
        })
        assert noted.json()["account_id"] == "11"
        assert client.get("/accounts/22/market/subscriptions", headers=headers).json()["subscriptions"] == []
        events = client.get("/accounts/11/events", headers=headers).json()["events"]
        assert {item["kind"] for item in events} >= {"execution", "gap", "subscription"}
    blob = b"".join(path.read_bytes() for path in (tmp_path / "state").glob("book.sqlite*"))
    assert b"secret-one" not in blob and b"secret-two" not in blob and b"key-one" not in blob
    assert numeric_id("11") == 11


def test_api_does_not_own_live_nodes_feed_processes_or_the_engine_book():
    offenders = []
    for path in (PROJECT / "api").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "LiveNode" in text or "account_node(" in text:
            offenders.append(str(path))
        if path.name != "engine_client.py" and "book.sqlite" in text:
            offenders.append(str(path))
    trading = (PROJECT / "api/exchanges/trading.py").read_text(encoding="utf-8")
    service = (PROJECT / "api/exchanges/service.py").read_text(encoding="utf-8")
    equity = (PROJECT / "api/equity/service.py").read_text(encoding="utf-8")
    subscriptions = (PROJECT / "api/market_data/subscriptions.py").read_text(encoding="utf-8")
    jobs = (PROJECT / "api/market_data/service.py").read_text(encoding="utf-8")
    assert not offenders
    assert "manual.execute" not in trading
    assert "queries.account_snapshot" not in service
    assert "engine.connectors.binance.valuation" not in equity
    assert "from engine.runtime import valuation_events" in equity
    assert "subprocess.Popen" not in subscriptions
    assert "engine.supervisor import popen" in subscriptions
    assert "engine.pipeline.pair_data" in jobs and "from engine.supervisor import popen as start_worker" in jobs
    config = (PROJECT / "api/config.py").read_text(encoding="utf-8")
    assert 'os.getenv("ENGINE_URL", "http://127.0.0.1:8766")' in config


def test_connectors_stay_below_feed_pipeline_and_api():
    for path in (PROJECT / "engine/connectors").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        forbidden = [
            name for name in modules
            if name.split(".", 1)[0] in {"feed", "pipeline", "api", "web"} or name.startswith("engine.feed")
        ]
        assert not forbidden, f"{path} imports {forbidden}"


def test_strategy_packages_remain_at_the_repository_root():
    assert (PROJECT / "strategy_python").is_dir()
    assert not (PROJECT / "engine/strategy_python").exists()
    assert (PROJECT / "engine/feed/managed.py").is_file()
    assert (PROJECT / "engine/connectors/binance/queries.py").is_file()
    assert (PROJECT / "engine/pipeline/pair_data.py").is_file()
    assert not (PROJECT / "feed").exists()
    assert not (PROJECT / "connectors").exists()
    assert not (PROJECT / "pipeline/pair_data.py").exists()
