import test from "node:test";
import assert from "node:assert/strict";
import { selection, realtimeStatus, taskAffects, type FeedStatus } from "./market-data-api.ts";

test("offline ranges use UTC and exclusive end without a fixed day cap", () => {
  const value = selection("ETHUSDT", "2026-08-01", "2026-08-02", "1m");
  assert.equal(value.start, "2026-08-01T00:00:00.000Z");
  assert.equal(value.end, "2026-08-02T00:00:00.000Z");
  assert.throws(() => selection("ETHUSDT", "2026-08-01", "2026-08-01", "1m"), /结束日期须晚于开始日期/);
  assert.equal(selection("ETHUSDT", "2026-01-01", "2026-08-01", "1m").start, "2026-01-01T00:00:00.000Z");
  assert.throws(() => selection("ETHUSDT", "2099-01-01", "2099-01-02", "1m"));
  assert.equal(selection("ETHUSDT", "2026-01-01", "2026-10-28", "tick").end, "2026-10-28T00:00:00.000Z");
  assert.equal(selection("ETHUSDC", "2024-01-01", "2026-10-28", "1m").start, "2024-01-01T00:00:00.000Z");
  assert.equal(selection("ETHUSDT", "2026-08-01", "2026-09-01", "tick").end, "2026-09-01T00:00:00.000Z");
});

test("historical data level selects ticks only or one bar period", () => {
  assert.deepEqual(selection("ETHUSDT", "2026-08-01", "2026-08-02", "tick").intervals, []);
  for (const period of ["1s", "1m", "5m", "15m"]) {
    assert.deepEqual(selection("ETHUSDT", "2026-08-01", "2026-08-02", period).intervals, [period]);
  }
});

test("realtime status requires supported level, matching subscription and fresh READY evidence", () => {
  const feed: FeedStatus = { state: "READY", data_ready: true, reason: "", checked_ms: 1000,
    valid_until_ms: 3000, symbols: { ETHUSDT: { next_ms: 1000, last_websocket_ms: 1000 } } };
  assert.equal(realtimeStatus(feed, "ETHUSDT", "1m", 2000), "运行中");
  assert.equal(realtimeStatus(feed, "ETHUSDT", "tick", 2000), "未接入");
  assert.equal(realtimeStatus(feed, "ETHUSDT", "5m", 2000), "未接入");
  assert.equal(realtimeStatus(feed, "BTCUSDT", "1m", 2000), "未订阅");
  assert.equal(realtimeStatus(feed, "ETHUSDT", "1m", 3001), "已过期");
  assert.equal(realtimeStatus({ ...feed, data_ready: false }, "ETHUSDT", "1m", 2000), "待确认");
  assert.equal(realtimeStatus(null, "ETHUSDT", "1m", 2000), "状态未知");
  assert.equal(realtimeStatus({ ...feed, symbols: {} }, "ETHUSDT", "1m", 2000), "待确认");
});

test("tasks attach only to their symbol and affected data levels", () => {
  const job = { action: "prepare", parameters: { symbol: "ETHUSDT", intervals: ["1m"] } };
  const item = { symbol: "ETHUSDT", level: "tick", event_ranges_ns: [], quality: "unverified" };
  assert.equal(taskAffects(job, item), true);
  assert.equal(taskAffects(job, { ...item, level: "1m" }), true);
  assert.equal(taskAffects(job, { ...item, level: "5m" }), false);
  assert.equal(taskAffects(job, { ...item, symbol: "BTCUSDT" }), false);
  assert.equal(taskAffects({ ...job, action: "delete" }, { ...item, level: "5m" }), true);
  assert.equal(taskAffects({ ...job, action: "trim" }, { ...item, level: "1M" }), true);
  assert.equal(taskAffects({ ...job, action: "trim" }, { ...item, symbol: "BTCUSDT" }), false);
  assert.equal(taskAffects({ ...job, action: "delete_pair" }, { ...item, level: "1M" }), true);
  assert.equal(taskAffects({ ...job, action: "delete_pair" }, { ...item, symbol: "BTCUSDT" }), false);
  assert.equal(taskAffects({ ...job, action: "delete_pair" }, { ...item, product: "options" }), false);
});

test("operation results ignore initial history and unchanged polling snapshots", async () => {
  const { taskTransitions } = await import("./market-data-api.ts");
  const previous = new Map([["active", "running"], ["old", "failed"]]);
  const snapshot = [{ id: "active", status: "succeeded" }, { id: "old", status: "failed" },
    { id: "history", status: "succeeded" }, { id: "new", status: "running" }];
  assert.deepEqual(taskTransitions(previous, snapshot).map((job) => job.id), ["active", "new"]);
  assert.deepEqual(taskTransitions(new Map(snapshot.map((job) => [job.id, job.status])), snapshot), []);
});

test("data rows come from stored data, live subscriptions and active tasks without presets", async () => {
  const { managedItems } = await import("./market-data-api.ts");
  assert.deepEqual(managedItems([], null, []), []);
  const stored = [{ symbol: "XRPUSDT", level: "tick", event_ranges_ns: [["1", "2"]], quality: "unverified" }];
  const jobs = [{ id: "a", status: "running", parameters: { symbol: "ADAUSDT", intervals: ["1m"] } },
    { id: "old", status: "succeeded", parameters: { symbol: "SOLUSDT", intervals: [] } }];
  const items = managedItems(stored, { symbols: { XRPUSDT: { next_ms: null, last_websocket_ms: null } } }, jobs);
  assert.deepEqual(items.map((item) => `${item.symbol}:${item.level}`), ["ADAUSDT:tick", "ADAUSDT:1m", "XRPUSDT:tick", "XRPUSDT:1m"]);
  assert.deepEqual(items.find((item) => item.symbol === "XRPUSDT" && item.level === "tick")?.event_ranges_ns, [["1", "2"]]);
});

test("USDC subscriptions and tasks retain their own symbols and data rows", async () => {
  const { managedItems } = await import("./market-data-api.ts");
  const items = managedItems([], { symbols: { ETHUSDC: { next_ms: null, last_websocket_ms: null } } },
    [{ status: "running", parameters: { symbol: "ETHUSDC", intervals: ["1s"] } }]);
  assert.deepEqual(items.map((item) => item.level), ["1m", "tick", "1s"]);
  assert.ok(items.every((item) => item.symbol === "ETHUSDC"));
});

test("option selections carry product without inventing historical Tick", () => {
  const recent = selection("BTC-261225-85000-C", "", "", "tick", "options", true);
  assert.equal(recent.product, "options");
  assert.deepEqual(recent.intervals, []);
  assert.ok(Date.now() - Date.parse(recent.end) < 2000);
  assert.ok(Date.parse(recent.end) - Date.parse(recent.start) <= 15 * 60000);
  const history = selection("BTC-261225-85000-C", "2026-08-01", "2026-08-02", "1m", "options");
  assert.equal(history.product, "options");
  assert.deepEqual(history.intervals, ["1m"]);
});

test("option K line tasks create only K line rows and never inherit perpetual readiness", async () => {
  const { managedItems, taskAffects, realtimeStatus, dataQuality } = await import("./market-data-api.ts");
  const parameters = { symbol: "BTC-261225-85000-C", product: "options", intervals: ["1m"] };
  const rows = managedItems([], null, [{ status: "running", parameters }]);
  assert.deepEqual(rows.map((row) => row.level), ["1m"]);
  assert.equal(rows[0].product, "options");
  assert.equal(taskAffects({ action: "prepare", parameters }, { ...rows[0], level: "tick" }), false);
  assert.equal(taskAffects({ action: "prepare", parameters }, { ...rows[0], product: "futures_um" }), false);
  assert.equal(realtimeStatus(null, parameters.symbol, "1m", Date.now(), "options"), "未接入");
  assert.equal(dataQuality({ ...rows[0], level: "tick", event_ranges_ns: [["1", "2"]] }), "近期片段 · 不完整");
});

import { groupDataItems, subscriptionState, type Subscription } from "./market-data-api.ts";

test("one symbol groups stored levels without inventing coverage of the configured base", () => {
  const config: Subscription = { policy: { symbol: "ETHUSDT", product: "futures_um", base_level: "1m", intervals: ["5m"], realtime_enabled: false },
    runtime: { state: "STOPPED", data_ready: false, reason: "disabled", checked_ms: null } };
  const grouped = groupDataItems([
    { symbol: "ETHUSDT", level: "tick", event_ranges_ns: [["1", "2"]], quality: "unverified" },
    { symbol: "ETHUSDT", level: "5m", event_ranges_ns: [["3", "4"]], quality: "unverified" },
    { symbol: "BTC-261225-85000-C", product: "options", level: "1m", event_ranges_ns: [], quality: "unverified" },
  ], [config]);
  assert.equal(grouped.length, 2);
  assert.equal(grouped[0].level, "1m");
  assert.deepEqual(grouped[0].levels, ["tick", "1m", "5m"]);
  assert.deepEqual(grouped[0].event_ranges_ns, []);
  assert.equal(subscriptionState(config, 100), "仅历史数据");
});

test("managed subscription state expires and does not confuse connection with readiness", () => {
  const config: Subscription = { policy: { symbol: "ETHUSDC", product: "futures_um", base_level: "1m", intervals: [], realtime_enabled: true },
    runtime: { state: "READY", data_ready: true, reason: "continuous_closed_minutes", checked_ms: 10, valid_until_ms: 100 } };
  assert.equal(subscriptionState(config, 50), "运行中");
  assert.equal(subscriptionState(config, 101), "已过期");
  assert.equal(subscriptionState({ ...config, runtime: { ...config.runtime, data_ready: false, state: "DEGRADED" } }, 50), "补缺或降级");
  assert.equal(groupDataItems([], [config])[0].symbol, "ETHUSDC");
});

test("automatic policies show actual stored levels instead of inventing an auto data level", () => {
  const policy: Subscription = { policy: { symbol: "ETHUSDT", product: "futures_um", base_level: "auto", intervals: ["5m", "15m"], realtime_enabled: false },
    runtime: { state: "STOPPED", data_ready: false, reason: "disabled", checked_ms: null } };
  const stored = [{ symbol: "ETHUSDT", level: "tick", event_ranges_ns: [["1", "2"]], quality: "unverified" }];
  assert.equal(groupDataItems(stored, [policy])[0].level, "tick");
  assert.equal(groupDataItems([], [policy])[0].level, "5m");
});


test("task exclusion is scoped to product and pair, including unconfirmed workers", async () => {
  const { hasPairTask } = await import("./market-data-api.ts");
  const tasks = [{ status: "running", parameters: { symbol: "ETHUSDT" } }];
  assert.equal(hasPairTask(tasks, "ETHUSDT"), true);
  assert.equal(hasPairTask(tasks, "BTCUSDT"), false);
  assert.equal(hasPairTask(tasks, "ETHUSDT", "options"), false);
  assert.equal(hasPairTask([{ ...tasks[0], status: "unknown" }], "ETHUSDT"), true);
  assert.equal(hasPairTask([{ ...tasks[0], status: "succeeded" }], "ETHUSDT"), false);
});


test("request timeouts use readable text while preserving the ambiguous-write timeout type", async (t) => {
  const { dataRequest } = await import("./market-data-api.ts");
  t.mock.method(globalThis, "fetch", async () => { throw new DOMException("signal timed out", "TimeoutError"); });
  await assert.rejects(dataRequest("jobs", { action: "prepare" }), (error: unknown) =>
    error instanceof DOMException && error.name === "TimeoutError" && error.message === "请求超时，请稍后重试。");
});

test("timeout while reading the response body is not mistaken for successful empty data", async (t) => {
  const { dataRequest } = await import("./market-data-api.ts");
  const controller = new AbortController();
  const response = new Response("{}");
  t.mock.method(response, "json", async () => {
    controller.abort(new DOMException("signal timed out", "TimeoutError"));
    throw controller.signal.reason;
  });
  t.mock.method(globalThis, "fetch", async () => response);
  await assert.rejects(dataRequest("datasets", undefined, controller.signal), (error: unknown) =>
    error instanceof DOMException && error.name === "TimeoutError");
});

test("navigation cancellation keeps AbortError for stale-request suppression", async (t) => {
  const { dataRequest } = await import("./market-data-api.ts");
  const aborted = new DOMException("cancelled", "AbortError");
  t.mock.method(globalThis, "fetch", async () => { throw aborted; });
  await assert.rejects(dataRequest("datasets"), (error: unknown) => error === aborted);
});
