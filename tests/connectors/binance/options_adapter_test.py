"""Options client against native LiveNode and real local WebSocket transport."""

import asyncio
import gc
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from nautilus_trader.common import DataActor, Environment, LoggerConfig, LogLevel
from nautilus_trader.config import ImportableConfig, ImportableFactoryConfig
from nautilus_trader.core import UUID4
from nautilus_trader.live import LiveNode
from nautilus_trader.model import BarType, BookType, InstrumentId, TraderId
from websockets.asyncio.server import serve

from connectors.binance import options, options_client, options_parsing
from connectors.binance.options_client import (
    BinanceOptionsDataClient,
    BinanceOptionsDataClientConfig,
    BinanceOptionsDataClientFactory,
)
from tests.connectors.binance.options_queries_test import exchange_info

SYMBOL = "BTC-261225-85000-C"
ID = InstrumentId.from_str(SYMBOL + ".BINANCE")
BASE = 1793145600000
BAR = BarType.from_str(f"{ID}-1-MINUTE-LAST-EXTERNAL")


def model(monkeypatch):
    monkeypatch.setattr(options, "get", lambda *a, **k: exchange_info())
    return options.instrument(options.markets()[0])


def test_config_identity_and_importable_roundtrip():
    config = BinanceOptionsDataClientConfig(symbols=[SYMBOL])
    restored = ImportableConfig.parse(config.to_importable(ImportableFactoryConfig(
        "connectors.binance.options_client:BinanceOptionsDataClientFactory")).json()).create()
    assert restored.symbols == [SYMBOL]
    for symbols in [[SYMBOL, SYMBOL], ["BTCUSDT"]]:
        with pytest.raises(ValueError):
            BinanceOptionsDataClientConfig(symbols=symbols)


def test_payload_precision_closed_bars_and_full_depth(monkeypatch):
    item = model(monkeypatch)
    tick = options_parsing.trade(item, {"id": 7, "time": BASE, "price": "1000.000", "qty": "0.25", "side": -1}, ts_init=BASE * 1_000_000 + 123)
    assert tick.ts_init != tick.ts_event and str(tick.aggressor_side) == "SELL"
    with pytest.raises(ValueError):
        options_parsing.trade(item, {"id": 7, "time": BASE, "price": "1000.0001", "qty": "0.25", "side": 1})
    with pytest.raises(ValueError):
        options_parsing.bar(item, "1m", [BASE, "1", "2", "1", "1", "0", BASE + 60000])
    row = {"s": SYMBOL, "T": BASE, "u": 10, "b": [["1000", "0.10"]], "a": [["1100", "0.20"]]}
    depth = options_parsing.depth(item, row, 5, BASE * 1_000_000 + 1)
    assert depth.sequence == 10 and len(depth.bids) == 1
    row["a"] = [["900", "0.20"]]
    with pytest.raises(ValueError):
        options_parsing.depth(item, row, 5, 1)


def test_native_node_streams_history_reconnect_unsubscribe_and_shutdown(monkeypatch):
    async def scenario():
        item = model(monkeypatch)
        monkeypatch.setattr(options, "markets", lambda: [item.info["binance_options"]])
        monkeypatch.setattr(options, "get", lambda endpoint, **kw: (
            ([[BASE, "1000", "1100", "900", "1050", "0.25", BASE + 59999]] if kw["startTime"] == BASE else []) if endpoint == "klines" else
            [{"id": 1, "time": BASE, "price": "1000", "qty": "0.10", "side": 1}]))
        clients, trades, quotes, books, bars, history = [], [], [], [], [], []
        ready, complete = asyncio.Event(), asyncio.Event()
        trade_connections = 0

        class Factory(BinanceOptionsDataClientFactory):
            @staticmethod
            def create(**kwargs):
                client = BinanceOptionsDataClientFactory.create(**kwargs)
                clients.append(client)
                return client

        class Actor(DataActor):
            def on_start(self):
                self.subscribe_trades(ID, client_id=clients[0].client_id)
                self.subscribe_quotes(ID, client_id=clients[0].client_id)
                self.subscribe_book_depth(ID, BookType.L2_MBP, depth=5, client_id=clients[0].client_id)
                self.subscribe_bars(BAR, client_id=clients[0].client_id)
                self.request_bars(BAR, datetime.fromtimestamp(BASE / 1000, UTC),
                                  datetime.fromtimestamp((BASE + 60000) / 1000, UTC),
                                  client_id=clients[0].client_id)
                ready.set()

            def on_trade(self, value):
                trades.append(value)
                if len(trades) == 2:
                    complete.set()

            def on_quote(self, value):
                quotes.append(value)

            def on_book_depth(self, value):
                books.append(value)

            def on_bar(self, value):
                bars.append(value)

            def on_historical_bars(self, values):
                history.extend(values)

        async def handler(socket):
            nonlocal trade_connections
            request = json.loads(await socket.recv())
            stream = request["params"][0]
            await socket.send(json.dumps({"result": None, "id": request["id"]}))
            if "@optionTrade" in stream:
                trade_connections += 1
                row = {"e": "trade", "E": BASE, "T": BASE + trade_connections, "s": SYMBOL,
                       "t": trade_connections, "p": "1000", "q": "0.10", "m": False, "X": "MARKET"}
                await socket.send(json.dumps(row))
                await socket.send(json.dumps(row))  # exactly repeated evidence is suppressed
                if trade_connections == 1:
                    await socket.close()
                    return
            elif "@bookTicker" in stream:
                await socket.send(json.dumps({"s": SYMBOL, "T": BASE, "b": "1000", "a": "1100", "B": "0.10", "A": "0.20"}))
            elif "@depth" in stream:
                await socket.send(json.dumps({"e": "depthUpdate", "s": SYMBOL, "T": BASE, "u": 10,
                                              "b": [["1000", "0.10"]], "a": [["1100", "0.20"]]}))
            else:
                row = {"e": "kline", "s": SYMBOL, "k": {"t": BASE, "T": BASE + 59999, "s": SYMBOL,
                       "i": "1m", "o": "1000", "h": "1100", "l": "900", "c": "1050", "v": "0.25", "x": False}}
                await socket.send(json.dumps(row))
                row["k"]["x"] = True
                await socket.send(json.dumps(row))
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            address = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            monkeypatch.setattr(options_client, "PUBLIC_WS", address)
            monkeypatch.setattr(options_client, "MARKET_WS", address)
            builder = LiveNode.builder("options-test", TraderId("OPTIONS-001"), Environment.LIVE)
            builder.with_logging(LoggerConfig(stdout_level=LogLevel.OFF))
            builder.with_delay_post_stop_secs(0).with_delay_shutdown_secs(0)
            builder.with_timeout_connection(10).with_timeout_disconnection_secs(5)
            builder.add_data_client("BINANCE_OPTIONS", Factory(), BinanceOptionsDataClientConfig(symbols=[SYMBOL]))
            node = builder.build()
            actor = Actor()
            node.add_actor(actor)
            cache = node.cache
            task = asyncio.create_task(node.run_async())
            try:
                await asyncio.wait_for(ready.wait(), 10)
                await asyncio.wait_for(complete.wait(), 12)
                for _ in range(100):
                    if quotes and books and bars and history:
                        break
                    await asyncio.sleep(.02)
                assert len(trades) == 2 and quotes and books and len(bars) == 1 and history
                assert cache.instrument(ID).multiplier == item.multiplier
                assert cache.quote(ID).bid_price == quotes[-1].bid_price
                state = clients[0].stream_status[f"{SYMBOL.lower()}@optionTrade"]
                assert state["connected"] and state["gap_count"] == 1
                assert "未补齐" in state["last_error"]
                actor.unsubscribe_quotes(ID, client_id=clients[0].client_id)
                for _ in range(100):
                    if f"{SYMBOL.lower()}@bookTicker" not in clients[0].streams:
                        break
                    await asyncio.sleep(.02)
                assert f"{SYMBOL.lower()}@bookTicker" not in clients[0].streams
            finally:
                node.handle().stop()
                await asyncio.wait_for(task, 15)
                node.dispose()
            assert not clients[0].streams
    asyncio.run(scenario())
    # Native clocks/loggers must be released on their owning thread, before later
    # HTTP worker allocations can trigger Python's cyclic collector there.
    gc.collect()


def test_bounded_requests_preserve_correlation_and_reject_history_tick(monkeypatch):
    async def scenario():
        item = model(monkeypatch)
        client = BinanceOptionsDataClient(name="OPTIONS", config=BinanceOptionsDataClientConfig(symbols=[SYMBOL]),
                                          cache=None, clock=SimpleNamespace(timestamp_ns=lambda: BASE * 1_000_000 + 120_000_000_000))
        client.instruments[SYMBOL] = item
        responses = []
        monkeypatch.setattr(client, "_handle_response", responses.append)
        request = SimpleNamespace(instrument_id=ID, start_ns=None, end_ns=None, limit=2,
                                  request_id=UUID4(), params={"scope": "recent"})
        monkeypatch.setattr(options, "get", lambda *a, **k: [{"id": 1, "time": BASE, "price": "1000", "qty": "0.1", "side": 1}])
        await client._request_trades(request)
        assert responses[0].correlation_id == request.request_id and responses[0].params == request.params
        request.start_ns = BASE * 1_000_000
        with pytest.raises(ValueError, match="历史 Tick"):
            await client._request_trades(request)
    asyncio.run(scenario())


def test_ack_rejection_closes_socket_and_does_not_admit_stream(monkeypatch):
    async def scenario():
        closed = []

        class Socket:
            async def send(self, payload):
                assert json.loads(payload)["method"] == "SUBSCRIBE"

            async def recv(self):
                return json.dumps({"id": 1, "code": -1, "msg": "invalid stream"})

            async def close(self):
                closed.append(True)

        async def connect(*a, **k):
            return Socket()

        monkeypatch.setattr(options_client, "connect", connect)
        client = BinanceOptionsDataClient(name="OPTIONS", config=BinanceOptionsDataClientConfig(),
                                          cache=None, clock=SimpleNamespace(timestamp_ns=lambda: BASE * 1_000_000))
        with pytest.raises(ValueError, match="订阅被拒绝"):
            await client._subscribe_stream("example", lambda value: value)
        assert closed == [True] and client.stream_status == {}
    asyncio.run(scenario())


def test_history_filters_event_boundaries_and_rejects_nonadvancing_pages(monkeypatch):
    async def scenario():
        item = model(monkeypatch)
        client = BinanceOptionsDataClient(name="OPTIONS", config=BinanceOptionsDataClientConfig(),
                                          cache=None, clock=SimpleNamespace(timestamp_ns=lambda: (BASE + 180000) * 1_000_000))
        client.instruments[SYMBOL] = item
        responses, calls = [], []
        monkeypatch.setattr(client, "_handle_response", responses.append)
        row = [BASE, "1000", "1100", "900", "1050", "0.25", BASE + 59999]

        def get(endpoint, **params):
            calls.append(params)
            return [row] if params["startTime"] == BASE else []

        monkeypatch.setattr(options, "get", get)
        request = SimpleNamespace(bar_type=BAR, start_ns=(BASE + 10000) * 1_000_000,
                                  end_ns=(BASE + 60000) * 1_000_000, limit=10, request_id=UUID4(), params={"case": "bounds"})
        await client._request_bars(request)
        response = responses[0]
        assert calls[0]["startTime"] == BASE
        assert len(response.data) == 1 and response.data[0].ts_init == response.data[0].ts_event
        assert response.start == request.start_ns and response.end == request.end_ns
        assert response.correlation_id == request.request_id and response.params == request.params
        monkeypatch.setattr(options, "get", lambda *a, **k: [row])
        with pytest.raises(ValueError, match="分页时间倒退"):
            await client._request_bars(request)
    asyncio.run(scenario())


def test_disconnect_closes_all_sockets_concurrently():
    async def scenario():
        count, all_started = 0, asyncio.Event()

        class Socket:
            async def close(self):
                nonlocal count
                count += 1
                if count == 3:
                    all_started.set()
                await all_started.wait()

        client = BinanceOptionsDataClient(name="OPTIONS", config=BinanceOptionsDataClientConfig(),
                                          cache=None, clock=None)
        client.streams = {str(i): {"socket": Socket(), "task": None} for i in range(3)}
        await asyncio.wait_for(client._disconnect(), 1)
        assert count == 3 and client.streams == {}
    asyncio.run(scenario())


def test_conflicting_live_trade_and_closed_bar_fail_instead_of_overwriting(monkeypatch):
    item = model(monkeypatch)
    client = BinanceOptionsDataClient(name="OPTIONS", config=BinanceOptionsDataClientConfig(),
                                      cache=None, clock=SimpleNamespace(timestamp_ns=lambda: BASE * 1_000_000 + 120_000_000_000))
    delivered = []
    monkeypatch.setattr(client, "_handle_data", delivered.append)
    state = {"parser": lambda row: options_parsing.trade(item, row), "last_event_ns": None}
    packet = {"tradeId": 1, "time": BASE, "price": "1000", "qty": "0.10", "side": 1}
    client._publish("test", state, packet)
    client._publish("test", state, packet)
    with pytest.raises(ValueError, match="编号冲突"):
        client._publish("test", state, packet | {"price": "1100"})
    assert len(delivered) == 1
    state = {"parser": lambda row: options_parsing.bar(item, "1m", row), "bar_evidence": None, "last_event_ns": None}
    packet = [BASE, "1000", "1100", "900", "1050", "0.25", BASE + 59999]
    client._publish("bar", state, packet)
    client._publish("bar", state, packet)
    packet[4] = "1060"
    with pytest.raises(ValueError, match="重复值冲突"):
        client._publish("bar", state, packet)
    assert len(delivered) == 2
