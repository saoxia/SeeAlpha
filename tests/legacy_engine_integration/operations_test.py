"""Ledger identity, evidence preservation, cursor ties, and authenticated API boundaries."""

import os
import uuid

import pytest

from api.db.database import Database
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


# Legacy app-lifespan regressions opt in to the in-process Engine fixture.
pytestmark = pytest.mark.usefixtures("engine_http")

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


def cash_event(**values):
    return {"event_id": "income-1", "account_id": 1, "account_name": "main",
            "exchange": "binance", "environment": "production", "symbol": "",
            "event_type": "cashflow", "timestamp": 1000, "evidence": "venue",
            "amount": "-0.0000000123456789", "asset": "USDC", "income_type": "FUNDING_FEE",
            "transaction_id": "9007199254740993", "info": "", "status": "confirmed", **values}


