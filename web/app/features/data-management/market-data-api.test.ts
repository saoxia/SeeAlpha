import test from "node:test";
import assert from "node:assert/strict";
import { selection, taskAffects } from "./market-data-api.ts";

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

import { groupDataItems, subscriptionState, hasPairTask, type Subscription } from "./market-data-api.ts";

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
  assert.equal(subscriptionState({ ...config, policy: { ...config.policy, product: "options" },
    runtime: { ...config.runtime, state: "STOPPED", data_ready: false, reason: "expired" } }, 1000), "已到期");
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

test("options inventory groups strikes and expiries by underlying and keeps futures separate", () => {
  const grouped = groupDataItems([
    { symbol: "BTC-261225-85000-C", product: "options", level: "1m", event_ranges_ns: [["5", "6"]], quality: "unverified" },
    { symbol: "BTC-270129-90000-P", product: "options", level: "tick", event_ranges_ns: [["1", "9"]], quality: "partial_recent_trades" },
    { symbol: "ETH-270129-3000-C", product: "options", level: "1m", event_ranges_ns: [], quality: "unverified" },
    { symbol: "BTCUSDT", level: "1m", event_ranges_ns: [["3", "4"]], quality: "unverified" },
  ], []);
  assert.equal(grouped.length, 3);
  const btc = grouped.find((item) => item.symbol === "BTCUSDT" && item.product === "options")!;
  assert.deepEqual(btc.levels, ["tick", "1m"]);
  assert.deepEqual(btc.event_ranges_ns, [["1", "9"], ["5", "6"]]);
  assert.equal(grouped.some((item) => item.symbol.includes("-")), false);
  assert.equal(hasPairTask([{ status: "running", parameters: { symbol: "BTC-261225-85000-C", product: "options" } }], "BTCUSDT", "options"), true);
  assert.equal(hasPairTask([{ status: "running", parameters: { symbol: "BTC-261225-85000-C", product: "options" } }], "ETHUSDT", "options"), false);
});

test("configured pairs appear without catalogue coverage, including stopped futures and options", () => {
  const config: Subscription = { policy: { symbol: "ETHUSDC", product: "futures_um", base_level: "tick", intervals: ["1s", "1m"], realtime_enabled: false,
    history_enabled: true, history_start: "2026-01-01T00:00:00Z" },
    runtime: { state: "STOPPED", data_ready: false, reason: "disabled", checked_ms: null } };
  const rows = groupDataItems([], [config, { ...config, policy: { ...config.policy, symbol: "ETHUSDT", base_level: "1m", intervals: ["1m", "5m"], product: "options" } }]);
  assert.deepEqual(rows.map(({ symbol, product, level, levels }) => ({ symbol, product, level, levels })), [
    { symbol: "ETHUSDC", product: "futures_um", level: "tick", levels: ["tick", "1s", "1m"] },
    { symbol: "ETHUSDT", product: "options", level: "1m", levels: ["1m", "5m"] },
  ]);
  assert.ok(rows.every((row) => row.event_ranges_ns.length === 0));
  assert.deepEqual(groupDataItems([], []), []);
});
