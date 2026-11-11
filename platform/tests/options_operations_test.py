"""Options basis/replay and recovery use fake client evidence, never Engine sources."""

import asyncio
import os
import time
import uuid
from types import SimpleNamespace

import pytest
from api.db.database import Database
from api.operations import options as module
from api.operations.options import position_view, sync_options
from api.operations.repository import Operations
from core.engine_contracts import QueryError

SYMBOL = "BTC-261225-85000-C"
OTHER = "ETH-261225-2500-P"


def fill(stamp, identity="1", side="BUY", quantity="1", price="100", symbol=SYMBOL):
    return {"symbol": symbol, "tradeId": identity, "orderId": "9007199254740993", "time": stamp,
            "side": side, "type": "LIMIT", "quantity": quantity, "price": price,
            "fee": "-0.010000001", "quoteAsset": "USDT"}


@pytest.fixture
def source(monkeypatch):
    clock = [int(time.time() * 1000)]
    account = {"id": 1, "account_name": "main", "exchange": "binance", "environment": "production",
               "credentials_encrypted": "encrypted-test-account", "credentials": {"apiKey": "test", "secret": "different"}}
    data = {"positions": [{"symbol": SYMBOL, "quantity": "2", "entryPrice": "100", "expiryDate": clock[0] + 86400000,
                            "quoteAsset": "USDT", "time": clock[0] - 1000}],
            "bills": [], "fills": [], "exercises": [], "orders": [], "open_orders": [],
            "position_calls": 0, "fail": None, "queries": []}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def positions(self):
            data["position_calls"] += 1
            return data["positions"]

        async def open_orders(self):
            return {"orders": data["open_orders"], "possibly_truncated": False}

        async def order_history(self, symbol, first, last):
            return {"orders": [row for row in data["orders"] if row["symbol"] == symbol], "possibly_truncated": False}

        async def history_pages(self, endpoint, first, last, *, symbol=None):
            data["queries"].append((endpoint, first, last, symbol))
            if endpoint == data["fail"]:
                raise QueryError("期权历史读取失败。")
            rows = data[{"bill": "bills", "userTrades": "fills", "exerciseRecord": "exercises"}[endpoint]]
            stamp = "time" if endpoint == "userTrades" else "createDate"
            yield {"start_ms": first, "end_ms": last,
                   "rows": [row for row in rows if first <= row[stamp] <= last and (symbol is None or row["symbol"] == symbol)]}

    class FakeEngine:
        def options_client(self, account):
            return Client()

        async def options_server_time(self, account):
            return {"serverTime": clock[0]}

        async def operation_history(self, account, **kwargs):
            raise QueryError("Binance 账户查询失败。")

        async def income_history(self, account, first, last):
            yield {"start_ms": first, "end_ms": last, "records": []}

    account["_test_engine"] = FakeEngine()
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: clock[0] / 1000))
    return account, clock, data


def state_for(account):
    return {"account_id": account["id"], "environment": account["environment"], "status": "partial", "complete": False}


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


def test_initial_basis_then_only_ledger_replay_including_late_fills_and_restart(ledger, source):
    account, clock, data = source
    state = state_for(account)
    data["fills"] = [fill(clock[0] - 100, quantity="2")]
    asyncio.run(sync_options(ledger, account, state, clock[0] - 1000, clock[0], engine=account["_test_engine"]))
    assert data["position_calls"] == 1
    initial = state["options"]["baseline"]
    assert state["options"]["positions"][0]["contracts"] == "2"  # No double-counting pre-baseline trades.
    clock[0] += 10000
    data["fills"].extend([fill(clock[0] - 5000, "2", quantity="1", price="130"),
                           fill(clock[0] - 1000, "3", side="SELL", quantity="1", price="150")])
    asyncio.run(sync_options(ledger, account, state, clock[0] - 1000, clock[0], engine=account["_test_engine"]))
    position = state["options"]["positions"][0]
    assert position["contracts"] == "2" and position["entry_price"] == "110"
    assert data["position_calls"] == 1 and state["options"]["baseline"] == initial
    # A late trade is earlier than the previous checkpoint. Replay uses the durable
    # baseline and full deduplicated ledger, rather than adding only a new tail.
    clock[0] += 10000
    data["fills"].append(fill(clock[0] - 15000, "4", side="SELL", quantity="0.5", price="150"))
    state = ledger.sync_state(1)[0]
    asyncio.run(sync_options(ledger, account, state, clock[0] - 1000, clock[0], engine=account["_test_engine"]))
    assert state["options"]["positions"][0]["contracts"] == "1.5"
    asyncio.run(sync_options(ledger, account, state, clock[0] - 1000, clock[0], engine=account["_test_engine"]))
    assert len(ledger.list(event_type="fill")["records"]) == 4
    assert data["position_calls"] == 1
    view, warning = position_view(ledger, {**account, "account_name": "renamed"})
    assert view[0]["account_name"] == "renamed" and view[0]["contracts"] == "1.5"
    assert "标记价" in warning and "unrealized_pnl" not in view[0]


def test_unknown_contract_discovered_from_bills_and_cashflow_ids_do_not_collide(ledger, source):
    account, clock, data = source
    state = state_for(account)
    asyncio.run(sync_options(ledger, account, state, clock[0] - 1000, clock[0], engine=account["_test_engine"]))
    clock[0] += 10000
    data["bills"] = [{"id": "99", "asset": "USDT", "amount": "-100", "type": "CONTRACT",
                       "symbol": OTHER, "createDate": clock[0] - 1}]
    data["fills"] = [fill(clock[0] - 1, "50", symbol=OTHER)]
    asyncio.run(sync_options(ledger, account, state, clock[0] - 1000, clock[0], engine=account["_test_engine"]))
    assert {row["symbol"] for row in state["options"]["positions"]} == {SYMBOL, OTHER}
    cash = ledger.list(event_type="cashflow")["records"][0]
    assert cash["income_type"] == "OPTIONS_CONTRACT" and cash["transaction_id"] == "99"
    assert cash["amount"] == "-100" and cash["source"] == "external"
    trade = ledger.list(event_type="fill")["records"][0]
    assert trade["fee_cost"] == "0.010000001" and trade["order_id"] == "9007199254740993"
    assert data["position_calls"] == 1


def test_baseline_boundary_activity_is_not_accepted_or_double_counted(ledger, source):
    account, clock, data = source
    state = state_for(account)
    data["fills"] = [fill(clock[0])]
    with pytest.raises(QueryError, match="稳定边界"):
        asyncio.run(sync_options(ledger, account, state, clock[0] - 1000, clock[0], engine=account["_test_engine"]))
    assert "baseline" not in state["options"]
    assert len(ledger.list(event_type="fill")["records"]) == 1
    clock[0] += 5000
    data["positions"][0]["quantity"] = "3"
    asyncio.run(sync_options(ledger, account, state, clock[0] - 10000, clock[0], engine=account["_test_engine"]))
    assert state["options"]["positions"][0]["contracts"] == "3"
    assert len(ledger.list(event_type="fill")["records"]) == 1


def test_failure_keeps_baseline_projection_and_checkpoint_for_recovery(ledger, source):
    account, clock, data = source
    state = state_for(account)
    asyncio.run(sync_options(ledger, account, state, clock[0] - 1000, clock[0], engine=account["_test_engine"]))
    previous = dict(state["options"])
    clock[0] += 10000
    data["exercises"] = [exercise(clock[0] - 1, quantity="2")]
    data["fail"] = "userTrades"
    with pytest.raises(QueryError):
        asyncio.run(sync_options(ledger, account, state, clock[0] - 1000, clock[0], engine=account["_test_engine"]))
    saved = ledger.sync_state(1)[0]
    assert saved["options"]["next_ms"] == previous["next_ms"]
    assert saved["options"]["baseline"] == previous["baseline"]
    assert saved["options"]["positions"] == previous["positions"]
    assert len(saved["options"]["exercises"]) == 1  # Evidence survives a later read failure.
    data["fail"] = None
    asyncio.run(sync_options(ledger, account, saved, clock[0] - 1000, clock[0], engine=account["_test_engine"]))
    assert saved["options"]["positions"] == []
    assert data["position_calls"] == 1
    data["exercises"][0]["quantity"] = "1"
    with pytest.raises(QueryError, match="身份内容冲突"):
        asyncio.run(sync_options(ledger, account, saved, clock[0] - 1000, clock[0], engine=account["_test_engine"]))


def test_retention_gap_and_changed_credentials_do_not_rebase_automatically(ledger, source):
    account, clock, data = source
    state = state_for(account)
    asyncio.run(sync_options(ledger, account, state, clock[0] - 1000, clock[0], engine=account["_test_engine"]))
    with pytest.raises(QueryError, match="凭据已变化"):
        asyncio.run(sync_options(ledger, {**account, "credentials_encrypted": "changed"}, state, clock[0] - 1, clock[0], engine=account["_test_engine"]))
    state["options"]["next_ms"] = clock[0] - 88 * module.DAY_MS
    with pytest.raises(QueryError, match="保留期"):
        asyncio.run(sync_options(ledger, account, state, clock[0] - 1, clock[0], engine=account["_test_engine"]))
    assert data["position_calls"] == 1


def test_cashflow_only_sync_does_not_acquire_or_replace_a_position_baseline(ledger, source):
    account, clock, data = source
    state = state_for(account)
    data["bills"] = [{"id": "1", "asset": "USDT", "amount": "-1", "type": "TRANSFER", "createDate": clock[0]}]
    counts = asyncio.run(sync_options(ledger, account, state, clock[0] - 1000, clock[0], cashflows_only=True, engine=account["_test_engine"]))
    assert counts == (1, 1)
    assert data["position_calls"] == 0 and {query[0] for query in data["queries"]} == {"bill"}
    assert "baseline" not in state["options"] and "next_ms" not in state["options"]
    asyncio.run(sync_options(ledger, account, state, clock[0] - 1000, clock[0], engine=account["_test_engine"]))
    initial = state["options"]["baseline"]
    clock[0] += 10000
    asyncio.run(sync_options(ledger, account, state, clock[0] - 1000, clock[0], cashflows_only=True, engine=account["_test_engine"]))
    assert state["options"]["baseline"] == initial and data["position_calls"] == 1


def test_option_ingestion_is_independent_of_futures_query_failure_and_recovers(ledger, source, monkeypatch):
    from api.operations import service as service_module
    from api.operations.service import OperationService

    account, clock, data = source
    accounts = SimpleNamespace(private=lambda _: account, row=lambda _: account,
                               repository=SimpleNamespace(list_exchange_accounts=lambda: [account]))
    service = OperationService(ledger.database, accounts, engine=account["_test_engine"])
    first = asyncio.run(service.sync(start_ms=clock[0] - 1000, end_ms=clock[0]))
    assert first["sync"][0]["status"] == "failed"
    assert first["sync"][0]["options"]["positions"][0]["contracts"] == "2"
    assert data["position_calls"] == 1
    # Only a fill in a later confirmed interval changes the projected quantity.
    clock[0] += 1000
    data["fills"] = [fill(clock[0] - 1, "10", "SELL", "1")]
    monkeypatch.setattr(service_module, "time", SimpleNamespace(time=lambda: clock[0] / 1000))
    recovered = asyncio.run(service.sync(start_ms=clock[0] - 1000, end_ms=clock[0]))
    assert recovered["sync"][0]["options"]["positions"][0]["contracts"] == "1"
    assert recovered["records_added"] == 1 and data["position_calls"] == 1


def test_late_fill_in_initial_acquisition_millisecond_remains_replayable(ledger, source):
    account, clock, data = source
    state = state_for(account)
    acquisition_ms = clock[0]
    asyncio.run(sync_options(ledger, account, state, clock[0] - 1000, clock[0], engine=account["_test_engine"]))
    assert state["options"]["baseline"]["at_ms"] == acquisition_ms - 1
    data["fills"] = [fill(acquisition_ms, "1234", quantity="1")]
    # Exchange trade identifiers are numeric; this record arrived after the
    # initial REST response but shares the acquisition timestamp.
    clock[0] += 1000
    asyncio.run(sync_options(ledger, account, state, clock[0] - 1000, clock[0], engine=account["_test_engine"]))
    assert state["options"]["positions"][0]["contracts"] == "3"
    assert data["position_calls"] == 1


def test_initial_account_access_failure_can_recover_without_a_false_identity_conflict(ledger, source):
    account, clock, data = source
    state = state_for(account)
    state["options"] = {"status": "failed", "message": "期权账户暂不可读。"}
    asyncio.run(sync_options(ledger, account, state, clock[0] - 1000, clock[0], engine=account["_test_engine"]))
    assert state["options"]["positions"][0]["contracts"] == "2" and data["position_calls"] == 1


def test_business_operations_require_engine_and_never_import_native_connectors(tmp_path):
    import ast
    from pathlib import Path

    from api.operations.service import OperationService

    with pytest.raises(ValueError, match="Engine HTTP"):
        OperationService(SimpleNamespace(backend="not-orm"), None, None)

    root = Path(__file__).resolve().parents[2] / "api"
    for relative in ("operations/service.py", "operations/options.py",
                     "exchanges/service.py", "exchanges/trading.py", "equity/service.py"):
        source = (root / relative).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert not node.module.startswith("engine."), (relative, node.module)
            if isinstance(node, ast.Import):
                assert not any(a.name.startswith("engine.") for a in node.names), relative
