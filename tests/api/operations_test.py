"""Ledger identity, evidence preservation, cursor ties, and authenticated API boundaries."""

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import AuthRepository
from api.db.database import Database
from api.main import create_app
from api.operations.repository import Operations


def event(**values):
    return {
        "event_id": "submit-1",
        "account_id": 1,
        "account_name": "main",
        "exchange": "binance",
        "environment": "production",
        "symbol": "ETHUSDT-PERP",
        "event_type": "place",
        "timestamp": 1000,
        "evidence": "project",
        "client_order_id": "client-1",
        "strategy_id": "strategy-1",
        "amount": "1",
        "price": "2000",
    } | values


@pytest.fixture(params=["legacy", "orm", "postgresql"])
def ledger(tmp_path, request):
    if request.param in {"orm", "postgresql"}:
        from alembic import command
        from alembic.config import Config

        from api.db.orm import ORMDatabase
        from api.operations.orm_repository import ORMOperations

        if request.param == "postgresql":
            url = os.environ.get("TEST_POSTGRES_URL")
            if not url:
                pytest.skip("TEST_POSTGRES_URL 未设置")
            schema = "test_" + uuid.uuid4().hex
        else:
            url = f"sqlite:///{tmp_path / 'orm.db'}"
            schema = None
        config = Config("alembic.ini")
        config.attributes.update(database_url=url, database_schema=schema)
        command.upgrade(config, "head")
        db = ORMDatabase(url, tmp_path / "orm.db", schema=schema)
        db.initialize()
        yield ORMOperations(db)
        db.close()
        if schema:
            from sqlalchemy import create_engine, text

            engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            engine.dispose()
        return
    db = Database(tmp_path / "api.db")
    db.initialize()
    repository = Operations(db)
    repository.initialize()
    yield repository
    db.close()


def test_project_venue_union_and_late_attribution(ledger):
    venue = event(
        event_id="venue-fill",
        event_type="fill",
        evidence="venue",
        strategy_id=None,
        order_id="venue-1",
        trade_id="trade-1",
        price="2001",
    )
    result = ledger.ingest(venue)
    assert ledger.list()["records"][0]["source"] == "external"
    ledger.ingest(event(event_id="accepted", order_id="venue-1"))
    record = ledger.detail(result["operation_id"])["operation"]
    assert record["source"] == "project" and record["strategy_id"] == "strategy-1"
    # The project's copy of the same fill enriches attribution, not financial facts.
    ledger.ingest(
        event(
            event_id="project-fill",
            event_type="fill",
            order_id="venue-1",
            trade_id="trade-1",
            price="2000",
        )
    )
    detail = ledger.detail(result["operation_id"])
    assert detail["operation"]["price"] == "2001"
    assert len(detail["observations"]) == 2
    assert len(ledger.list()["records"]) == 2
    assert not ledger.ingest(venue)["observation_added"]


def test_partial_fills_accounts_environment_and_order_identity(ledger):
    for trade_id, account_id, environment in [
        ("t1", 1, "production"),
        ("t2", 1, "production"),
        ("t1", 2, "production"),
        ("t1", 1, "sandbox"),
    ]:
        ledger.ingest(
            event(
                event_id="fill-" + trade_id,
                event_type="fill",
                trade_id=trade_id,
                order_id="v1",
                evidence="venue",
                strategy_id=None,
                account_id=account_id,
                environment=environment,
            )
        )
    assert len(ledger.list()["records"]) == 4
    assert len(ledger.list(account_id=1, environment="production")["records"]) == 2


def test_unknown_is_external_and_correction_never_fill(ledger):
    ledger.ingest(event(evidence="venue", strategy_id=None))
    ledger.ingest(
        event(
            event_id="correction",
            evidence="reconciliation",
            strategy_id=None,
            event_type="correction",
        )
    )
    assert {r["source"] for r in ledger.list()["records"]} == {"external", "reconciliation"}
    with pytest.raises(ValueError):
        ledger.ingest(event(evidence="reconciliation", event_type="fill", trade_id="fake"))
    with pytest.raises(ValueError):
        ledger.ingest(event(event_type="fill"))
    with pytest.raises(ValueError):
        ledger.ingest(event(evidence="venue"))


def test_repeated_events_preserve_evidence_and_conflicts(ledger):
    first = ledger.ingest(event())
    assert not ledger.ingest(event())["created"]
    with pytest.raises(ValueError):
        ledger.ingest(event(price="999"))
    with pytest.raises(ValueError):
        ledger.ingest(event(event_id="wrong-owner", strategy_id="strategy-2"))
    assert ledger.detail(first["operation_id"])["operation"]["price"] == "2000"


def test_concurrent_replays_one_operation(ledger):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: ledger.ingest(event()), range(12)))
    assert sum(r["created"] for r in results) == 1
    assert len(ledger.list()["records"]) == 1


def test_same_millisecond_cursor_has_no_gaps_and_filters(ledger):
    for i in range(9):
        ledger.ingest(
            event(event_id=f"fill-{i}", event_type="fill", trade_id=str(i), order_id="v1")
        )
    seen = []
    cursor = None
    while True:
        page = ledger.list(
            cursor=cursor,
            limit=2,
            source="project",
            strategy_id="strategy-1",
            symbol="ETH",
            start_ms=1000,
            end_ms=1000,
        )
        seen.extend(r["id"] for r in page["records"])
        if not page["has_more"]:
            break
        r = page["records"][-1]
        cursor = [r["timestamp"], r["id"]]
    assert len(seen) == len(set(seen)) == 9
    assert ledger.list(symbol="%")["records"] == []


def test_api_pagination_details_export_and_auth(tmp_path):
    settings = Settings(tmp_path / "api.db", cookie_secure=False)
    db = Database(settings.database_path)
    db.initialize()
    AuthRepository(db).create_user("admin", hash_password("test-password-123"))
    app = create_app(settings)
    with TestClient(app) as client:
        repo = app.state.operations.repository
        repo.ingest(event())
        repo.ingest(event(event_id="second", client_order_id="client-2"))
        assert client.get("/api/operations").status_code == 401
        assert client.post("/api/operations/sync", json={}).status_code == 401
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin", "password": "test-password-123"}
            ).status_code
            == 200
        )
        page = client.get("/api/operations?limit=1").json()
        assert page["has_more"] and page["next_cursor"] and page["warnings"]
        import base64

        for bad in ([1000, 42], [10**50, "00000000-0000-0000-0000-000000000000"]):
            import json

            cursor = base64.urlsafe_b64encode(json.dumps(bad).encode()).decode()
            assert client.get("/api/operations", params={"cursor": cursor}).status_code == 422
        second = client.get("/api/operations", params={"cursor": page["next_cursor"]}).json()
        assert (
            len(second["records"]) == 1 and second["records"][0]["id"] != page["records"][0]["id"]
        )
        operation_id = page["records"][0]["id"]
        assert len(client.get("/api/operations/" + operation_id).json()["observations"]) == 1
        for params in (
            {"cursor": "invalid"},
            {"source": "manual"},
            {"limit": 0},
            {"start_ms": 2000, "end_ms": 1000},
        ):
            assert client.get("/api/operations", params=params).status_code == 422
        csv = client.get("/api/operations/export")
        assert csv.status_code == 200 and "strategy-1" in csv.text
        assert client.post("/api/operations/sync", json={"start_ms": 1}).status_code == 422


def test_sync_uses_native_reports_and_retains_previous_records(tmp_path, monkeypatch):
    import asyncio

    from api.db.exchange_repository import exchange_repository
    from api.exchanges.accounts import Accounts
    from api.operations import service as module
    from api.operations.service import OperationService

    db = Database(tmp_path / "api.db")
    db.initialize()
    accounts = Accounts(db, Settings(db.path))
    # Stub credential access rather than save real or fake network credentials in this test.
    exchange_repository(db).create_exchange_account(
        exchange="binance",
        account_name="main",
        environment="production",
        credentials_encrypted="test",
        market_symbols_json="[]",
    )
    monkeypatch.setattr(accounts, "private", lambda _: {"id": 1, "environment": "production"})
    service = OperationService(db, accounts)
    service.repository.initialize()
    now = int(time.time() * 1000)

    async def reports(account, **options):
        return [event(timestamp=now, evidence="venue", strategy_id=None)]

    monkeypatch.setattr(module, "operation_history", reports)
    result = asyncio.run(service.sync())
    assert result["records_added"] == 1 and result["sync"][0]["status"] == "partial"
    assert asyncio.run(service.sync())["records_added"] == 0

    async def failed(*args, **kwargs):
        raise RuntimeError("secret must not leak")

    monkeypatch.setattr(module, "operation_history", failed)
    result = asyncio.run(service.sync())
    assert result["sync"][0]["status"] == "failed" and "secret" not in str(result)
    assert len(service.repository.list()["records"]) == 1


def test_late_identifier_bridge_merges_duplicate_order_and_preserves_links(ledger):
    first = ledger.ingest(event())
    second = ledger.ingest(
        event(
            event_id="venue-only",
            client_order_id=None,
            order_id="42",
            evidence="venue",
            strategy_id=None,
            price="2001",
        )
    )
    assert len(ledger.list()["records"]) == 2
    linked = ledger.ingest(event(event_id="accepted", order_id="42"))
    rows = ledger.list()["records"]
    assert len(rows) == 1 and rows[0]["id"] == first["operation_id"] == linked["operation_id"]
    assert rows[0]["price"] == "2001" and rows[0]["confirmed"]
    assert len(ledger.detail(second["operation_id"])["observations"]) == 3


def test_native_project_event_sink_does_not_confuse_external_claims(ledger):
    from types import SimpleNamespace

    from nautilus_trader import model as m
    from nautilus_trader.core import UUID4

    from api.operations.service import OperationService
    from connectors.binance.operations import cached_order_observations, normalize_event

    account = {"id": 1, "account_name": "main", "exchange": "binance", "environment": "production"}
    trader = m.TraderId("TEST-001")
    strategy = m.StrategyId("STRATEGY-001")
    instrument = m.InstrumentId.from_str("ETHUSDT-PERP.BINANCE")
    client = m.ClientOrderId("project-order")
    params = {
        "trader_id": trader,
        "strategy_id": strategy,
        "instrument_id": instrument,
        "client_order_id": client,
        "venue_order_id": m.VenueOrderId("42"),
        "account_id": m.AccountId("BINANCE-1"),
        "trade_id": m.TradeId("99"),
        "order_side": m.OrderSide.BUY,
        "order_type": m.OrderType.LIMIT,
        "last_qty": m.Quantity.from_str("0.010"),
        "last_px": m.Price.from_str("2000.00"),
        "currency": m.Currency.from_str("USDT"),
        "liquidity_side": m.LiquiditySide.TAKER,
        "event_id": UUID4(),
        "ts_event": 2_000_000_000,
        "ts_init": 2_000_000_000,
        "reconciliation": True,
        "commission": m.Money(0.01, m.Currency.from_str("USDT")),
    }
    fill = m.OrderFilled(**params)
    order = SimpleNamespace(
        strategy_id=strategy,
        tags=[],
        instrument_id=instrument,
        client_order_id=client,
        venue_order_id=m.VenueOrderId("42"),
        side=m.OrderSide.BUY,
        order_type=m.OrderType.LIMIT,
        quantity=m.Quantity.from_str("0.010"),
        price=m.Price.from_str("2000.00"),
        events=lambda: [fill],
    )
    # Strategy ownership acquired through claims is not project submission evidence.
    external = normalize_event(fill, order, account)
    assert external["evidence"] == "venue" and external["strategy_id"] is None
    submitted = m.OrderSubmitted(
        trader_id=trader,
        strategy_id=strategy,
        instrument_id=instrument,
        client_order_id=client,
        account_id=m.AccountId("BINANCE-1"),
        event_id=UUID4(),
        ts_event=1_000_000_000,
        ts_init=1_000_000_000,
    )
    order.events = lambda: [submitted, fill]
    service = OperationService(ledger.database, None)
    service.record_project_event(submitted, order, account)
    service.record_project_event(fill, order, account)
    assert {r["source"] for r in ledger.list()["records"]} == {"project"}
    inferred = m.OrderFilled(
        **(params | {"trade_id": m.TradeId("INFERRED-1"), "event_id": UUID4()})
    )
    correction = normalize_event(inferred, order, account)
    assert correction["event_type"] == "correction" and correction["strategy_id"] is None
    order.tags = ["RECONCILIATION"]
    assert cached_order_observations(order, account) == []


def test_distinct_venue_order_ids_are_not_merged_by_reused_client_id(ledger):
    for venue_id in ("42", "43"):
        ledger.ingest(
            event(
                event_id="venue-" + venue_id, order_id=venue_id, evidence="venue", strategy_id=None
            )
        )
    records = ledger.list()["records"]
    assert len(records) == 2 and {r["order_id"] for r in records} == {"42", "43"}
