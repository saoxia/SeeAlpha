import test from "node:test";
import assert from "node:assert/strict";
import { shiftDay } from "./date-range.ts";

test("UTC day offsets support leap years and year boundaries", () => {
  assert.equal(shiftDay("2024-02-28", 1), "2024-02-29");
  assert.equal(shiftDay("2027-01-01", -1), "2026-12-31");
});
