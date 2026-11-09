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


class FakeEngine:
    """Exercise the Engine HTTP contract without native Binance fallback."""

    async def income_history(self, _account, start_ms, end_ms):
        yield {"start_ms": start_ms, "end_ms": end_ms, "records": []}

    async def operation_history(self, _account, *, lookback_minutes=1440):
        return []


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
    from api.operations.service import OperationService

    db = Database(tmp_path / "api.db")
    db.initialize()
    accounts = Accounts(db, Settings(db.path))
    # Stub credential access rather than save real or fake network credentials in this test.
    exchange_repository(db).create_exchange_account(
        tenant_id=1,
        exchange="binance",
        account_name="main",
        environment="production",
        credentials_encrypted="test",
        market_symbols_json="[]",
    )
    monkeypatch.setattr(accounts, "private", lambda _: {"id": 1, "environment": "production"})
    engine = FakeEngine()
    service = OperationService(db, accounts, engine)
    service.repository.initialize()
    now = int(time.time() * 1000)

    async def reports(account, **options):
        return [event(timestamp=now, evidence="venue", strategy_id=None)]

    monkeypatch.setattr(engine, "operation_history", reports)
    async def incomes(*args):
        yield {"start_ms": args[1], "end_ms": args[2], "records": []}

    monkeypatch.setattr(engine, "income_history", incomes)
    result = asyncio.run(service.sync())
    assert result["records_added"] == 1 and result["sync"][0]["status"] == "partial"
    assert asyncio.run(service.sync())["records_added"] == 0

    async def failed(*args, **kwargs):
        raise RuntimeError("secret must not leak")

    monkeypatch.setattr(engine, "operation_history", failed)
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

    from engine.connectors.binance.operations import cached_order_observations, normalize_event

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
    ledger.ingest(normalize_event(submitted, order, account))
    ledger.ingest(normalize_event(fill, order, account))
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


def cash_event(**values):
    return {"event_id": "income-1", "account_id": 1, "account_name": "main",
            "exchange": "binance", "environment": "production", "symbol": "",
            "event_type": "cashflow", "timestamp": 1000, "evidence": "venue",
            "amount": "-0.0000000123456789", "asset": "USDC", "income_type": "FUNDING_FEE",
            "transaction_id": "9007199254740993", "info": "", "status": "confirmed", **values}


def test_cashflows_keep_sign_currency_identity_and_never_merge_into_orders(ledger):
    first = ledger.ingest(cash_event())
    assert not ledger.ingest(cash_event())["created"]
    assert not ledger.ingest(cash_event(account_name="renamed"))["created"]
    for change in ({"amount": "1"}, {"asset": "USDT"}, {"symbol": "ETHUSDC"}, {"timestamp": 1001}):
        with pytest.raises(ValueError):
            ledger.ingest(cash_event(**change))
    ledger.ingest(cash_event(income_type="COMMISSION", trade_id="17"))
    ledger.ingest(cash_event(account_id=2))
    ledger.ingest(cash_event(environment="sandbox"))
    ledger.ingest(event(event_type="fill", trade_id="17", evidence="venue", strategy_id=None))
    records = ledger.list()["records"]
    assert len(records) == 5
    original = ledger.detail(first["operation_id"])
    assert len(original["observations"]) == 1
    assert original["operation"]["amount"] == "-0.0000000123456789"
    assert original["operation"]["asset"] == "USDC"
    assert original["operation"]["source"] == "external"
    assert "asset" not in next(item for item in records if item["event_type"] == "fill")


def test_concurrent_cashflow_replays_are_idempotent(ledger):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: ledger.ingest(cash_event()), range(10)))
    assert sum(item["created"] for item in results) == 1
    assert len(ledger.list()["records"]) == 1


def test_cashflow_boundary_rejects_missing_fields_and_fabricated_order_links(ledger):
    for change in ({"asset": None}, {"amount": None}, {"transaction_id": "1.5"},
                   {"evidence": "project"}, {"order_id": "17"}, {"strategy_id": "strategy"},
                   {"amount": "NaN"}, {"income_type": ""}):
        with pytest.raises(ValueError):
            ledger.ingest(cash_event(**change))
    with pytest.raises(ValueError):
        ledger.ingest(event(asset="USDT"))
    with pytest.raises(ValueError):
        ledger.ingest(event(symbol=""))


def test_income_pipeline_recovers_checkpoints_and_is_independent_of_native_failures(ledger, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from api.operations.service import OperationService
    from engine.connectors.binance.income import DAY_MS

    source = {"id": 1, "account_name": "main", "exchange": "binance", "environment": "production"}
    accounts = SimpleNamespace(private=lambda _: source,
                               repository=SimpleNamespace(list_exchange_accounts=lambda: [source]))
    engine = FakeEngine()
    service = OperationService(ledger.database, accounts, engine)
    starts = []
    first_stop = None

    async def incomplete(account, first, last):
        nonlocal first_stop
        starts.append(first)
        first_stop = first + DAY_MS - 1
        yield {"start_ms": first, "end_ms": first_stop,
               "records": [cash_event(timestamp=first)]}
        raise RuntimeError("private-signature-must-not-leak")

    async def native_failed(*args, **kwargs):
        raise RuntimeError("private-native-error")

    monkeypatch.setattr(engine, "income_history", incomplete)
    monkeypatch.setattr(engine, "operation_history", native_failed)
    result = asyncio.run(service.sync())
    state = result["sync"][0]
    assert result["records_added"] == 1 and state["status"] == "failed"
    cash = state["cashflows"]
    assert cash["next_ms"] == first_stop + 1
    assert cash["coverage"] == [[starts[0], first_stop]]
    assert not cash["requested_complete"] and "private-" not in str(result)

    async def recovered(account, first, last):
        starts.append(first)
        yield {"start_ms": first, "end_ms": last,
               "records": [cash_event(timestamp=starts[0]),
                           cash_event(event_id="income-2", transaction_id="2", timestamp=first_stop + 1)]}

    monkeypatch.setattr(engine, "income_history", recovered)
    # A new service simulates API restart; durable state is independent of service state.
    service = OperationService(ledger.database, accounts, engine)
    result = asyncio.run(service.sync(cashflows_only=True))
    cash = result["sync"][0]["cashflows"]
    assert starts[-1] <= first_stop + 1 and result["records_added"] == 1
    assert cash["status"] == "success" and cash["requested_complete"]
    assert cash["next_ms"] == cash["requested_end_ms"] + 1
    assert len(cash["coverage"]) == 1 and len(ledger.list()["records"]) == 2
    assert result["sync"][0]["status"] == "failed"  # Funds cannot certify order history.


def test_cancelled_income_pipeline_keeps_acknowledged_windows(ledger, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from api.operations.service import OperationService

    source = {"id": 1, "account_name": "main", "exchange": "binance", "environment": "production"}
    accounts = SimpleNamespace(private=lambda _: source,
                               repository=SimpleNamespace(list_exchange_accounts=lambda: [source]))

    async def cancelled(account, first, last):
        yield {"start_ms": first, "end_ms": first + 999, "records": []}
        raise asyncio.CancelledError

    engine = FakeEngine()
    monkeypatch.setattr(engine, "income_history", cancelled)
    service = OperationService(ledger.database, accounts, engine)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.sync(cashflows_only=True))
    cash = ledger.sync_state()[0]["cashflows"]
    assert cash["next_ms"] == cash["coverage"][0][1] + 1
    assert not cash["requested_complete"] and cash["status"] == "syncing"


def test_cashflow_api_list_export_and_sync_authorization(tmp_path, monkeypatch):
    settings = Settings(tmp_path / "api.db", cookie_secure=False)
    db = Database(settings.database_path)
    db.initialize()
    repository = AuthRepository(db)
    for name in ("admin", "viewer"):
        repository.create_user(name, hash_password("test-password-123"))
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.operations.repository.ingest(cash_event(info="=private-note"))
        assert client.get("/api/operations?event_type=cashflow").status_code == 401
        client.post("/api/auth/login", json={"username": "viewer", "password": "test-password-123"})
        record = client.get("/api/operations?event_type=cashflow").json()["records"][0]
        assert record["asset"] == "USDC" and record["transaction_id"] == "9007199254740993"
        assert record["price"] is None and record["order_id"] is None
        export = client.get("/api/operations/export?event_type=cashflow").text
        assert "income_type" in export and "USDC" in export and "'=private-note" in export
        assert client.post("/api/operations/sync", json={"cashflows_only": True}).status_code == 403

        async def sync(**values):
            assert values["cashflows_only"] is True
            return {"records_added": 0}

        monkeypatch.setattr(app.state.operations, "sync", sync)
        client.post("/api/auth/login", json={"username": "admin", "password": "test-password-123"})
        assert client.post("/api/operations/sync", json={"cashflows_only": True}).status_code == 200
        assert client.post("/api/operations/sync", json={"cashflows_only": True},
                           headers={"Origin": "https://untrusted.example"}).status_code == 403


def test_expired_income_checkpoint_does_not_certify_the_missing_history(ledger, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from api.operations.service import OperationService
    from engine.connectors.binance.income import DAY_MS

    now = int(time.time() * 1000)
    old = [now - 150 * DAY_MS, now - 101 * DAY_MS]
    ledger.save_sync(account_id=1, environment="production", status="partial", complete=False,
                     cashflows={"next_ms": old[1] + 1, "coverage": [old]})
    source = {"id": 1, "account_name": "main", "exchange": "binance", "environment": "production"}
    accounts = SimpleNamespace(private=lambda _: source,
                               repository=SimpleNamespace(list_exchange_accounts=lambda: [source]))

    async def empty(account, first, last):
        assert first >= now - 87 * DAY_MS
        yield {"start_ms": first, "end_ms": last, "records": []}

    engine = FakeEngine()
    monkeypatch.setattr(engine, "income_history", empty)
    result = asyncio.run(OperationService(ledger.database, accounts, engine).sync(cashflows_only=True))
    cash = result["sync"][0]["cashflows"]
    assert result["records_added"] == 0 and cash["requested_complete"]
    assert len(cash["coverage"]) == 2 and cash["coverage"][0] == old
    assert cash["coverage"][0][1] + 1 < cash["coverage"][1][0]
    assert not result["sync"][0]["complete"]
