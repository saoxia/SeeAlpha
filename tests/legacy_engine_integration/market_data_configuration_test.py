"""Date changes coordinate parameters, confirmed deletion and service recovery."""

from datetime import UTC, datetime

import pytest
from nautilus_trader.model import AggressorSide, Price, Quantity, TradeId, TradeTick

from engine.pipeline.catalog import ParquetDataCatalog
from engine.pipeline.models import PipelineSettings
from nautilus_support import TestInstrumentProvider
from tests.legacy_engine_integration.market_data_test import context
from tests.fixtures.api_auth import login

__all__ = ["context"]


# Legacy app-lifespan regressions opt in to the in-process Engine fixture.
pytestmark = pytest.mark.usefixtures("engine_http")

@pytest.fixture
def configured(context, monkeypatch):
    client, app, root = context
    login(client)
    manager = app.state.market_data
    sync_start = len(manager.engine.sync_calls)
    payload = {"symbol": "ETHUSDT", "base_level": "tick", "realtime_enabled": False,
               "history_enabled": True, "history_mode": "start", "history_start": "2026-08-01T00:00:00Z"}
    response = client.post("/api/data/subscriptions", json=payload)
    assert response.status_code == 200, response.text
    settings = PipelineSettings("futures_um", "trades", ("ETHUSDT",), ("1m",),
                                root / "data/raw", root / "data/catalog", root / "data/state")
    catalog = ParquetDataCatalog(settings.catalog_dir)
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    catalog.write_data([instrument])
    ticks = []
    source = settings.raw_dir / "futures/um/daily/trades/ETHUSDT"
    source.mkdir(parents=True)
    for index, day in enumerate(("2026-08-01", "2026-08-20")):
        stamp = int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1e9)
        ticks.append(TradeTick(instrument.id, Price.from_str("10.00"), Quantity.from_str("1.000"),
                               AggressorSide.BUY, TradeId(str(index + 1)), stamp, stamp))
        (source / f"ETHUSDT-trades-{day}.zip").write_bytes(b"archive")
    catalog.write_data(ticks)
    return client, manager, root, settings, catalog, source, payload, sync_start


def test_earlier_start_saves_and_restarts_history_without_deleting(configured):
    client, manager, _root, _settings, catalog, source, payload, sync_start = configured
    response = client.post("/api/data/subscriptions", json={**payload, "history_start": "2026-07-01T00:00:00Z"})
    assert response.status_code == 200 and "job" not in response.json()
    assert len(catalog.trade_ticks()) == 2 and len(list(source.glob("*.zip"))) == 2
    saved = manager.policy_repository.list()[0]
    assert saved["history_start"].startswith("2026-07-01") and saved["history_enabled"]
    assert len(manager.engine.sync_calls) > sync_start
    assert manager.engine.sync_calls[-1]["policies"][0]["history_start"] == "2026-07-01T00:00:00Z"


def test_later_start_requires_bound_confirmation_and_rejects_changed_sources(configured):
    client, manager, _root, _settings, catalog, source, payload, _starts = configured
    desired = {**payload, "history_start": "2026-08-15T00:00:00Z"}
    assert client.post("/api/data/subscriptions", json=desired).status_code == 409
    saved = manager.policy_repository.list()[0]
    assert not saved["history_enabled"] and saved["history_start"].startswith("2026-08-01")
    plan = client.post("/api/data/subscriptions/plan", json=desired)
    assert plan.status_code == 200, plan.text
    plan = plan.json()
    assert plan["requires_confirmation"] and plan["deletion"]["delete_archives"]
    assert client.post("/api/data/subscriptions", json={**desired, "deletion_snapshot": plan["deletion"]["snapshot"],
        "confirmation": "WRONG"}).status_code == 422
    (source / "ETHUSDT-trades-2026-08-01.zip").write_bytes(b"changed source")
    assert client.post("/api/data/subscriptions", json={**desired, "deletion_snapshot": plan["deletion"]["snapshot"],
        "confirmation": "DELETE ETHUSDT"}).status_code == 409
    assert len(catalog.trade_ticks()) == 2
    login(client, username="viewer")
    assert client.post("/api/data/subscriptions/plan", json=desired).status_code == 403
    login(client)
    assert client.post("/api/data/subscriptions/plan", json=desired, headers={"origin": "https://evil.example"}).status_code == 403


