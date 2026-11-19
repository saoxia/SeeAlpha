import assert from "node:assert/strict";
import test from "node:test";

import type { BasketBacktestResult } from "./api";
import { basketReportMetrics } from "./basket-report";

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
    fundingObservations: null, targetEvents: null,
  });
});
