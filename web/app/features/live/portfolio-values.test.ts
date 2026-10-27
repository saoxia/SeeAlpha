import assert from "node:assert/strict";
import test from "node:test";

import { formatAccountValue, hasValidAccountValueInvariant } from "./portfolio-values.ts";

test("formats account values consistently", () => {
  assert.equal(formatAccountValue("4202.48"), "$ 4,202.48");
  assert.equal(formatAccountValue(null), "$ --");
});

test("accepts wallet balance plus unrealized pnl as total equity", () => {
  assert.equal(
    hasValidAccountValueInvariant({
      wallet_balance: "5881.91",
      unrealized_pnl: "-1684.15",
      total_equity: "4197.76",
    }),
    true,
  );
});

test("rejects double-counted unrealized pnl", () => {
  assert.equal(
    hasValidAccountValueInvariant({
      wallet_balance: "5881.91",
      unrealized_pnl: "-1684.15",
      total_equity: "2513.61",
    }),
    false,
  );
});
