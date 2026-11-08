import test from "node:test";
import assert from "node:assert/strict";
import { isFresh, orderError, requestId, tradingRequest, type OrderRequest } from "./api.ts";

test("future timestamps and stale snapshots never enable manual orders", () => {
  assert.equal(isFresh(1000, 2000), true);
  assert.equal(isFresh(1000, 61000), false);
  assert.equal(isFresh(3000, 2000), false);
  assert.equal(isFresh(undefined, 2000), false);
});
test("order validation rejects zero, nonnumeric quantity, missing limit price and hedge reduce-only", () => {
  const order: OrderRequest = {account_revision: "f".repeat(64), request_id: "fixture", environment: "sandbox", symbol: "ETHUSDT-PERP", side: "buy", order_type: "limit", quantity: "0.01", price: "2000"};
  assert.equal(orderError(order), "");
  for (const changes of ([{quantity: "0"}, {quantity: "NaN"}, {quantity: "1e5"}, {price: null}, {position_side: "LONG", reduce_only: true}] as Partial<OrderRequest>[])) {
    assert.notEqual(orderError({...order, ...changes}), "");
  }
  assert.equal(orderError({...order, order_type: "market", price: null}), "");
});
test("failed writes are sent once with the original request identity", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => {calls++; throw new TypeError("connection lost");});
  await assert.rejects(tradingRequest("/api/trading/accounts/1/orders", {method: "POST", body: '{"request_id":"same"}'}));
  assert.equal(calls, 1);
});

test("manual request IDs remain UUIDs on LAN HTTP without randomUUID", t => {
  t.mock.method(globalThis.crypto, "randomUUID", () => {throw new Error("unavailable on LAN HTTP");});
  const first = requestId();
  assert.match(first, /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
  assert.notEqual(first, requestId());
});

test("proxy errors without JSON use a readable error and are never retried", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => {calls++; return new Response("Internal Server Error", {status: 502});});
  await assert.rejects(tradingRequest("/api/trading/accounts/1/state"), /HTTP 502/);
  assert.equal(calls, 1);
});

test("background account reads keep valid snapshots actionable but never bypass safety gates", async () => {
  const { orderBlockReason } = await import('./api.ts');
  const ready = {hasAccount:true, accountCheckedAt:1000, marketCheckedAt:1000, now:2000, busy:false, pending:false, loading:true, accountError:'', marketError:''};
  assert.equal(orderBlockReason(ready), '');
  assert.equal(orderBlockReason({...ready, now:61000}), '正在核对账户与委托…');
  assert.equal(orderBlockReason({...ready, loading:false, now:61000}), '账户快照已过期，请刷新账户。');
  for (const changes of [{hasAccount:false}, {busy:true}, {pending:true}, {accountError:'network'}, {marketError:'disconnected'}, {marketCheckedAt:undefined}, {accountCheckedAt:3000}]) {
    assert.notEqual(orderBlockReason({...ready,...changes}), '');
  }
  assert.equal(orderBlockReason({...ready, accountCheckedAt:61000, marketCheckedAt:61000, now:62000}), '');
});
