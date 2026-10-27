"""Exercise the real native execution factory using local signed-API fixtures only."""

import asyncio
import json
import threading
import time
from http.server import ThreadingHTTPServer
from typing import ClassVar
from urllib.parse import urlparse

import pytest
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from connectors.binance.queries import account_snapshot, instruments
from tests.connectors.binance.websocket_transport_test import Handler


class AccountHandler(Handler):
    calls: ClassVar[list] = []
    position_rows: ClassVar[list] = []

    def reply(self, data):
        raw = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path
        self.calls.append(("GET", path))
        if path.endswith("/account"):
            self.reply(
                {
                    "feeTier": 0,
                    "canTrade": True,
                    "canDeposit": True,
                    "canWithdraw": True,
                    "updateTime": int(time.time() * 1000),
                    "multiAssetsMargin": False,
                    "totalInitialMargin": "0",
                    "totalMaintMargin": "0",
                    "totalWalletBalance": "100",
                    "totalUnrealizedProfit": "0",
                    "totalMarginBalance": "100",
                    "totalPositionInitialMargin": "0",
                    "totalOpenOrderInitialMargin": "0",
                    "totalCrossWalletBalance": "100",
                    "totalCrossUnPnl": "0",
                    "availableBalance": "100",
                    "maxWithdrawAmount": "100",
                    "assets": [
                        {
                            "asset": "USDT",
                            "walletBalance": "100",
                            "unrealizedProfit": "0",
                            "marginBalance": "100",
                            "maintMargin": "0",
                            "initialMargin": "0",
                            "positionInitialMargin": "0",
                            "openOrderInitialMargin": "0",
                            "crossWalletBalance": "100",
                            "crossUnPnl": "0",
                            "availableBalance": "100",
                            "maxWithdrawAmount": "100",
                            "marginAvailable": True,
                            "updateTime": 0,
                        }
                    ],
                    "positions": [],
                }
            )
        elif path.endswith("/positionSide/dual"):
            self.reply({"dualSidePosition": len(self.position_rows) > 1})
        elif path.endswith("/multiAssetsMargin"):
            self.reply({"multiAssetsMargin": False})
        elif path.endswith("/listenKey"):
            self.reply({"listenKey": "local-fixture-stream"})
        elif path.endswith("/positionRisk"):
            self.reply(self.position_rows)
        elif path.endswith(("/openOrders", "/openAlgoOrders", "/userTrades", "/allOrders")):
            self.reply([])
        else:
            super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        self.calls.append(("POST", path))
        if path.endswith("/listenKey"):
            self.reply({"listenKey": "local-fixture-stream"})
        else:
            self.send_error(405)

    def do_DELETE(self):
        path = urlparse(self.path).path
        self.calls.append(("DELETE", path))
        if path.endswith("/listenKey"):
            self.reply({})
        else:
            self.send_error(405)

    def do_PUT(self):
        path = urlparse(self.path).path
        self.calls.append(("PUT", path))
        if path.endswith("/listenKey"):
            self.reply({})
        else:
            self.send_error(405)


@pytest.mark.parametrize("with_position", [False, True, "hedge"])
def test_native_catalog_and_account_snapshot_do_not_mutate_exchange(with_position):
    async def scenario():
        AccountHandler.calls = []
        AccountHandler.position_rows = (
            [
                {
                    "symbol": "ETHUSDT",
                    "positionAmt": "0.010",
                    "entryPrice": "2000",
                    "markPrice": "2010",
                    "unRealizedProfit": "0.1",
                    "liquidationPrice": "0",
                    "leverage": "5",
                    "marginType": "cross",
                    "isolatedMargin": "0",
                    "isAutoAddMargin": "false",
                    "positionSide": "BOTH",
                    "notional": "20.1",
                    "isolatedWallet": "0",
                    "updateTime": int(time.time() * 1000),
                    "marginAsset": "USDT",
                    "maxNotionalValue": "100000",
                }
            ]
            if with_position
            else []
        )
        if with_position == "hedge":
            row = AccountHandler.position_rows[0]
            AccountHandler.position_rows = [
                {**row, "positionSide": "LONG"},
                {**row, "positionSide": "SHORT", "positionAmt": "-0.020"},
            ]
        http = ThreadingHTTPServer(("127.0.0.1", 0), AccountHandler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()

        async def websocket(ws):
            try:
                async for raw in ws:
                    request = json.loads(raw)
                    await ws.send(json.dumps({"id": request.get("id"), "result": None}))
            except ConnectionClosed:
                pass

        try:
            async with serve(websocket, "127.0.0.1", 0) as server:
                http_url = f"http://127.0.0.1:{http.server_port}"
                websocket_url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
                catalog = await instruments("production", http_url=http_url)
                assert [str(i.id) for i in catalog] == ["ETHUSDT-PERP.BINANCE"]
                try:
                    snapshot = await account_snapshot(
                        {
                            "id": 1,
                            "environment": "production",
                            "credentials": {
                                "apiKey": "local-fixture-key",
                                "secret": "local-fixture-secret",
                            },
                        },
                        http_url=http_url,
                        websocket_url=websocket_url,
                        timeout=12,
                    )
                except Exception as exc:
                    if exc.__context__ is not None:
                        raise exc.__context__
                    raise
                assert snapshot.balances == {"USDT": "100.00000000"}
                assert snapshot.info["total_wallet_balance"] == "100"
                assert snapshot.info["total_margin_balance"] == "100"
                assert len(snapshot.positions) == len(AccountHandler.position_rows)
                if with_position:
                    assert snapshot.positions[0]["symbol"] == "ETH/USDT:USDT"
                    assert "0.010" in {p["contracts"] for p in snapshot.positions}
                if with_position == "hedge":
                    assert {p["side"] for p in snapshot.positions} == {"long", "short"}
                    assert len({p["id"] for p in snapshot.positions}) == 2
                assert any(path.endswith("/account") for _, path in AccountHandler.calls)
                assert all(
                    method == "GET" or path.endswith("/listenKey")
                    for method, path in AccountHandler.calls
                )
        finally:
            http.shutdown()
            http.server_close()
            thread.join(timeout=2)

    asyncio.run(scenario())
