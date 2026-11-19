import assert from "node:assert/strict";
import test from "node:test";

import type { BasketBacktestResult } from "./api";
import { basketReportMetrics } from "./basket-report.ts";

const base = {
  bar_count: 2,
  starting_balance: "1000",
  ending_balance: "1001",
  fills: [],
};

test("funding-carry report shows signed simulated legs and account equities", () => {
  const report: BasketBacktestResult = {
    ...base,
    mode: "native_dual_venue_development_only",
    equity_curve: [{
      ts_ns: 1, total_equity: "1001", spot_quantity: "0.2",
      perpetual_quantity: "-0.2", leg_drift: "0",
      spot_equity: "501", perpetual_equity: "500",
    }],
    funding_observations: [{ ts_ns: 1, rate: "0.0001" }],
  };
  const summary = basketReportMetrics(report);
  assert.deepEqual(summary.positions.map(row => row.label), [
    "现货模拟净持仓", "永续模拟净持仓（有符号）",
  ]);
  assert.equal(summary.positions[1].value, "-0.2");
  assert.equal(summary.legDrift, "0");
  assert.deepEqual(summary.accounts.map(row => row.value), ["501", "500"]);
  assert.equal(summary.fundingObservations, 1);
  assert.equal(summary.targetEvents, null);
});

test("option-protection report labels perpetual exposure as absolute", () => {
  const report: BasketBacktestResult = {
    ...base,
    mode: "native_protected_options_development_only",
    equity_curve: [{
      ts_ns: 2, total_equity: "1001", perpetual_quantity: "0.2",
      put_quantity: "0.3", call_quantity: "0.3",
      options_equity: "400", perpetual_equity: "601",
    }],
    target_events: [{ action: "enter_short" }],
  };
  const summary = basketReportMetrics(report);
  assert.deepEqual(summary.positions.map(row => row.label), [
    "永续模拟敞口（绝对数量）", "PUT 模拟持仓", "CALL 模拟持仓",
  ]);
  assert.equal(summary.targetEvents, 1);
  assert.equal(summary.legDrift, null);
  assert.equal(summary.fundingObservations, null);
});

test("missing optional data remains missing, not fabricated as zero", () => {
  const report: BasketBacktestResult = {
    ...base, mode: "native_dual_venue_development_only", equity_curve: [],
  };
  assert.deepEqual(basketReportMetrics(report), {
    positions: [], accounts: [], legDrift: null,
    fundingObservations: null, targetEvents: null, latestTargetEvent: null,
  });
});


test("native carry journal shows exact signed product targets and scoped member count", () => {
  const report: BasketBacktestResult = {
    ...base, mode: "native_dual_venue_development_only",
    equity_curve: [{ ts_ns: 200, total_equity: "1001" }],
    target_events: [{
      ts_ns: 200, member_count: 2, executable: false,
      targets: [
        { product: "spot", symbol: "ETHUSDT", quantity: "0.2" },
        { product: "futures_um", symbol: "ETHUSDT", quantity: "-0.2" },
      ],
    }],
  };
  const metrics = basketReportMetrics(report);
  assert.equal(metrics.targetEvents, 1);
  assert.deepEqual(metrics.latestTargetEvent, {
    tsNs: 200, memberCount: 2, action: null,
    targets: [
      { label: "spot/ETHUSDT", quantity: "0.2" },
      { label: "futures_um/ETHUSDT", quantity: "-0.2" },
    ],
  });
});

test("malformed target events cannot invent holdings or act as venue evidence", () => {
  const report: BasketBacktestResult = {
    ...base, mode: "native_protected_options_development_only",
    equity_curve: [],
    target_events: [{
      ts_ns: 101, targets: [
        { product: "spot", symbol: "ETHUSDT", quantity: "0.1" },
        { product: "spot", symbol: "ETHUSDT", quantity: "0.2" },
      ],
    }],
  };
  assert.equal(basketReportMetrics(report).targetEvents, 1);
  assert.equal(basketReportMetrics(report).latestTargetEvent, null);
  report.target_events = [{ ts_ns: 102, targets: [
    { product: "spot", symbol: "ETHUSDT", quantity: "Infinity" },
  ] }];
  assert.equal(basketReportMetrics(report).latestTargetEvent, null);
  report.target_events = [{ ts_ns: -1, targets: [
    { product: "spot", symbol: "ETHUSDT", quantity: "0" },
  ] }];
  assert.equal(basketReportMetrics(report).latestTargetEvent, null);
});

test("option history target keeps action separate from actual fills", () => {
  const report: BasketBacktestResult = {
    ...base, mode: "native_protected_options_development_only",
    equity_curve: [{ ts_ns: 103, total_equity: "1000" }],
    target_events: [{
      ts_ns: 103, action: "enter_short",
      targets: [
        { product: "options", symbol: "ETH-261225-1800-P", quantity: "0.1" },
        { product: "options", symbol: "ETH-261225-2200-C", quantity: "0.1" },
        { product: "futures_um", symbol: "ETHUSDT", quantity: "-0.1" },
      ],
    }],
  };
  const target = basketReportMetrics(report).latestTargetEvent;
  assert.equal(target?.action, "enter_short");
  assert.equal(target?.memberCount, null);
  assert.equal(target?.targets[2].quantity, "-0.1");
});
