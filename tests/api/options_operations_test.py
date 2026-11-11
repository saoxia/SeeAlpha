"""Options baseline/replay, durable evidence, recovery and existing API composition."""

import time

import pytest
from fastapi.testclient import TestClient

from api.operations.options import account_identity, replay_positions
from engine.connectors.binance.operations import normalize_option_record
from tests.api import exchanges_test
from tests.api.exchanges_test import collect, create, signin

application = exchanges_test.application

SYMBOL = "BTC-261225-85000-C"
OTHER = "ETH-261225-2500-P"


def fill(stamp, identity="1", side="BUY", quantity="1", price="100", symbol=SYMBOL):
    return {"symbol": symbol, "tradeId": identity, "orderId": "9007199254740993", "time": stamp,
            "side": side, "type": "LIMIT", "quantity": quantity, "price": price,
            "fee": "-0.010000001", "quoteAsset": "USDT"}


def exercise(stamp, identity="2", quantity="1", side="LONG"):
    return {"id": identity, "symbol": SYMBOL, "positionSide": side, "createDate": stamp,
            "quantity": quantity, "amount": "0", "fee": "0", "exercisePrice": "85000",
            "quoteAsset": "USDT"}


# Legacy app-lifespan regressions opt in to the in-process Engine fixture.
pytestmark = pytest.mark.usefixtures("engine_http")


def test_replay_partial_close_reversal_and_exercise_conflict():
    baseline = {"at_ms": 100, "rows": [{"symbol": SYMBOL, "quantity": "2", "entryPrice": "100"}]}
    account = {"id": 1, "account_name": "main", "exchange": "binance", "environment": "production"}
    records = [normalize_option_record(fill(101, "1", "SELL", "1", "150"), account, "fill"),
               normalize_option_record(fill(102, "2", "SELL", "2", "160"), account, "fill")]
    result = replay_positions(baseline, records, [], 102)[0]
    assert result["contracts"] == "1" and result["side"] == "short" and result["entry_price"] == "160"
    assert replay_positions(baseline, records, [exercise(103, side="SHORT")], 103) == []
    with pytest.raises(ValueError, match="行权数量"):
        replay_positions(baseline, records, [exercise(103, side="LONG")], 103)
    tied = [normalize_option_record(fill(101, "3", "BUY"), account, "fill"),
            normalize_option_record(fill(101, "4", "SELL", "0.5"), account, "fill")]
    assert replay_positions(baseline, tied, [], 101)[0]["entry_price"] is None


def test_home_reads_projection_without_putting_options_in_minute_files(application):
    app, _, _ = application
    with TestClient(app) as client:
        signin(client)
        account_id = create(client).json()["id"]
        account = app.state.accounts.row(account_id)
        repository = app.state.operations.repository
        repository.save_sync(account_id=account_id, environment="production", status="partial", complete=False,
                             options={"identity": account_identity(account), "baseline": {"at_ms": 1, "rows": []},
                                      "status": "partial", "last_success_at": int(time.time() * 1000),
                                      "message": "期权持仓由初始基准与操作推算。",
                                      "positions": [{"id": "options:example", "symbol": SYMBOL, "side": "long",
                                                     "contracts": "0.25", "entry_price": "100"}]})
        url = f"/api/exchange-accounts/{account_id}/portfolio"
        # Even when futures balances have not yet been collected, options remain visible.
        assert client.get(url).json()["positions"][0]["symbol"] == SYMBOL
        assert client.get("/api/exchange-accounts/portfolio").json()["positions"][0]["symbol"] == SYMBOL
        collect(client, account_id)
        assert len(client.get(url).json()["positions"]) == 2
        assert len(client.get(f"/api/exchange-accounts/{account_id}/positions").json()) == 2
        path = app.state.exchanges.portfolio_root / f"{account_id}.json"
        assert SYMBOL not in path.read_text()
        page = client.get("/api/operations").json()
        assert any("期权持仓" in warning for warning in page["warnings"])


