import test from "node:test";
import assert from "node:assert/strict";
import { applyMarketEvent, marketUrls, mergeCandle, subscribeMarket } from "./market-stream.ts";
import type { Market } from "./api.ts";

const candle = (time: number, close = "10") => ({time, open: "10", high: "12", low: "9", close, volume: "2"});
const market = {symbol: "ETHUSDT-PERP", environment: "production", interval: "1m", checked_at: 0,
  candles: [candle(60)], bids: [], asks: [], trades: [], depth_time: null, last_price: "10"} as unknown as Market;

test("forming updates replace one candle, boundary appends, late corrections remain sorted and bounded", () => {
  const rows = [candle(60), candle(120)];
  const updated = mergeCandle(rows, candle(120, "11"));
  assert.deepEqual(updated.map(r => r.time), [60, 120]);
  assert.equal(updated[1].close, "11");
  assert.equal(updated[0], rows[0]);
  assert.deepEqual(mergeCandle(updated, candle(180)).map(r => r.time), [60, 120, 180]);
  assert.equal(mergeCandle(Array.from({length: 300}, (_, i) => candle(i)), candle(300))[0].time, 1);
});

test("public routes separate depth from market data and isolate demo environment", () => {
  const production = marketUrls("production", "ETHUSDT-PERP", "15m");
  assert.match(production.market, /fstream\.binance\.com\/market\/stream\?.*ethusdt@kline_15m/);
  assert.match(production.book, /\/public\/stream\?.*ethusdt@depth20@100ms/);
  const demo = marketUrls("sandbox", "ETHUSDT-PERP", "1m");
  assert.match(demo.market, /demo-fstream/);
  assert.match(demo.rest, /demo-fapi/);
});

test("only matching kline events refresh candle readiness; other symbols and periods cannot leak", () => {
  const event = {e: "kline", E: 100000, s: "ETHUSDT", k: {t: 60000, i: "1m", o: "10", h: "12", l: "9", c: "11", v: "3"}};
  assert.equal(applyMarketEvent(market, {...event, s: "BTCUSDT"}), market);
  assert.equal(applyMarketEvent(market, {...event, k: {...event.k, i: "15m"}}), market);
  assert.equal(applyMarketEvent(market, event).checked_at, 100000);
  const print = applyMarketEvent(market, {e: "aggTrade", E: 100001, s: "ETHUSDT", a: 7, p: "12", q: "2", T: 100000, m: false});
  assert.equal(print.checked_at, 0);
  assert.equal(print.last_price, "12");
  assert.equal(print.trades[0].id, 7);
});

test("subscription buffers startup events, reports disconnect, and cleanup stops reconnect and late results", async t => {
  t.mock.timers.enable({apis: ["setTimeout", "setInterval"]});
  class Socket {
    static all: Socket[] = [];
    onmessage?: (event: {data: string}) => void;
    onerror?: () => void;
    onclose?: () => void;
    closed = false;
    url: string;
    constructor(url: string) {this.url = url; Socket.all.push(this);}
    close() {this.closed = true; this.onclose?.();}
  }
  t.mock.method(globalThis, "WebSocket", function (url: string) {return new Socket(url);});
  let failMetadata = false;
  const calls: RequestInit[] = [];
  t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
    calls.push(init);
    if (failMetadata && (url.includes("openInterest") || url.includes("fundingInfo"))) throw new Error("optional metadata unavailable");
    const payload = url.includes("klines") ? [[60000, "10", "12", "9", "10", "2"]] : url.includes("ticker") ? {lastPrice: "10",priceChange:"1",priceChangePercent:"10",volume:"12",quoteVolume:"120"} : url.includes("openInterest") ? {symbol:"ETHUSDT",openInterest:"100",time:Date.now()} : url.includes("fundingInfo") ? [{symbol:"ETHUSDT",fundingIntervalHours:4}] : {};
    return new Response(JSON.stringify(payload));
  });
  const updates: Market[] = [], errors: string[] = [];
  const stop = subscribeMarket("ETHUSDT-PERP", "production", "1m", m => updates.push(m), e => errors.push(e));
  Socket.all[0].onmessage?.({data: JSON.stringify({data: {e: "kline", E: Date.now(), s: "ETHUSDT", k: {t: 60000, i: "1m", o: "10", h: "12", l: "9", c: "11", v: "3"}}})});
  await new Promise<void>(resolve => setImmediate(resolve));
  assert.equal(updates.at(-1)?.candles[0].close, "11");
  assert.equal(calls.length, 5);
  assert.equal(updates.at(-1)?.open_interest, "100");
  assert.equal(updates.at(-1)?.funding_interval_hours, 4);
  assert.ok(calls.every(init => init.credentials === "omit"));
  failMetadata = true;
  Socket.all[1].onerror?.();
  assert.match(errors.at(-1)!, /中断/);
  t.mock.timers.tick(1000);
  await new Promise<void>(resolve => setImmediate(resolve));
  assert.equal(Socket.all.length, 4);
  assert.equal(calls.length, 10);
  assert.equal(updates.at(-1)?.open_interest, undefined);
  assert.equal(updates.at(-1)?.funding_interval_hours, undefined);
  const count = updates.length;
  stop();
  Socket.all[2].onmessage?.({data: JSON.stringify({data: {e: "kline", E: Date.now(), s: "ETHUSDT", k: {t: 60000, i: "1m", o: "10", h: "12", l: "9", c: "12", v: "4"}}})});
  t.mock.timers.tick(60000);
  assert.equal(Socket.all.length, 4);
  assert.ok(Socket.all.every(socket => socket.closed));
  assert.equal(updates.length, count);
});
