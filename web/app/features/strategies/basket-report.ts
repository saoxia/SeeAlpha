import type { BasketBacktestResult } from "./api";

export type BasketMetric = {
  label: string;
  value: string;
};

export type BasketHedge = { symbol: string; baseQuantityDriftLimit: string };

export type BasketTargetEvent = {
  tsMs: number;
  memberCount: number | null;
  action: string | null;
  targets: Array<{ label: string; quantity: string }>;
};

/** Runtime source is a JSON report; only expose complete, bounded records. */
function latestVerifiedTargetEvent(value: unknown): BasketTargetEvent | null {
  if (!Array.isArray(value) || value.length === 0) return null;
  const raw: unknown = value.at(-1);
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const row = raw as Record<string, unknown>;
  if (
    typeof row.ts_ns !== "number" || !Number.isFinite(row.ts_ns)
    || row.ts_ns <= 0 || !Number.isSafeInteger(Math.trunc(row.ts_ns / 1_000_000))
    || !Array.isArray(row.targets)
    || row.targets.length < 1 || row.targets.length > 64
  ) return null;
  const targets: BasketTargetEvent["targets"] = [];
  const seen = new Set<string>();
  for (const entry of row.targets) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) return null;
    const item = entry as Record<string, unknown>;
    if (
      !["spot", "futures_um", "options"].includes(String(item.product))
      || typeof item.symbol !== "string"
      || !/^[A-Z0-9_-]{1,64}$/.test(item.symbol)
      || typeof item.quantity !== "string"
      || !/^-?(?:\d+(?:\.\d+)?|\.\d+)$/.test(item.quantity)
    ) return null;
    const key = String(item.product) + "/" + item.symbol;
    if (seen.has(key)) return null;
    seen.add(key);
    targets.push({ label: key, quantity: item.quantity });
  }
  return {
    // Native nanoseconds exceed JS safe integer range; report only UTC ms.
    tsMs: Math.trunc(row.ts_ns / 1_000_000),
    memberCount: typeof row.member_count === "number"
      && Number.isInteger(row.member_count)
      && row.member_count >= 1 && row.member_count <= 16
      ? row.member_count : null,
    action: typeof row.action === "string"
      && ["enter_long", "enter_short", "exit"].includes(row.action)
      ? row.action : null,
    targets,
  };
}

/** Opt-in research hedge metadata only; malformed/partial claims are never displayed. */
function verifiedHedges(result: BasketBacktestResult): BasketHedge[] {
  const claims = result.declared_hedges;
  if (
    result.mode !== "native_generic_development_only"
    || result.hedge_verification !== "confirmed_phases_only_non_atomic"
    || !Array.isArray(claims) || claims.length < 1 || claims.length > 8
  ) return [];
  const seen = new Set<string>();
  const rows: BasketHedge[] = [];
  for (const claim of claims) {
    if (!claim || typeof claim !== "object" || Array.isArray(claim)) return [];
    const row = claim as Record<string, unknown>;
    if (
      typeof row.spot !== "string" || !/^[A-Z0-9-]{2,64}$/.test(row.spot)
      || row.spot !== row.perpetual
      || row.base_quantity_drift_limit !== "0"
      || seen.has(row.spot)
    ) return [];
    seen.add(row.spot);
    rows.push({ symbol: row.spot, baseQuantityDriftLimit: "0" });
  }
  return rows;
}

/** Only project values that actually exist in the Engine research report.
 * Never turn a simulated exposure into a claim about real account positions.
 */
export function basketReportMetrics(result: BasketBacktestResult): {
  positions: BasketMetric[];
  accounts: BasketMetric[];
  hedges: BasketHedge[];
  legDrift: string | null;
  fundingObservations: number | null;
  targetEvents: number | null;
  latestTargetEvent: BasketTargetEvent | null;
} {
  const last = result.equity_curve.at(-1);
  const positions: BasketMetric[] = [];
  const accounts: BasketMetric[] = [];
  if (last) {
    if (typeof last.spot_quantity === "string") {
      positions.push({ label: "现货模拟净持仓", value: last.spot_quantity });
    }
    if (typeof last.perpetual_quantity === "string") {
      positions.push({
        label: result.mode === "native_protected_options_development_only"
          ? "永续模拟敞口（绝对数量）" : "永续模拟净持仓（有符号）",
        value: last.perpetual_quantity,
      });
    }
    if (typeof last.put_quantity === "string") {
      positions.push({ label: "PUT 模拟持仓", value: last.put_quantity });
    }
    if (typeof last.call_quantity === "string") {
      positions.push({ label: "CALL 模拟持仓", value: last.call_quantity });
    }
    if (typeof last.spot_equity === "string") {
      accounts.push({ label: "现货模拟权益（USDT）", value: last.spot_equity });
    }
    if (typeof last.options_equity === "string") {
      accounts.push({ label: "期权模拟权益（USDT）", value: last.options_equity });
    }
    if (typeof last.perpetual_equity === "string") {
      accounts.push({ label: "永续模拟权益（USDT）", value: last.perpetual_equity });
    }
  }
  if (last && result.mode === "native_generic_development_only") {
    const seen = new Set<string>();
    for (const item of Array.isArray(last.positions) ? last.positions : []) {
      if (
        !item || !["spot", "futures_um"].includes(item.product)
        || typeof item.symbol !== "string" || !/^[A-Z0-9-]{2,64}$/.test(item.symbol)
        || typeof item.quantity !== "string" || !/^-?\d+(?:\.\d+)?$/.test(item.quantity)
      ) continue;
      const key = item.product + ":" + item.symbol;
      if (seen.has(key)) continue;
      seen.add(key);
      positions.push({ label: key + " 模拟净持仓", value: item.quantity });
    }
    const productEquity = last.product_equity;
    for (const product of ["spot", "futures_um"]) {
      const value = productEquity?.[product];
      if (typeof value === "string" && /^-?\d+(?:\.\d+)?$/.test(value)) {
        accounts.push({
          label: product === "spot" ? "现货 CASH 模拟权益（USDT）" : "永续 MARGIN 模拟权益（USDT）",
          value,
        });
      }
    }
  }
  return {
    positions,
    accounts,
    hedges: verifiedHedges(result),
    legDrift: typeof last?.leg_drift === "string" ? last.leg_drift : null,
    fundingObservations: Array.isArray(result.funding_observations)
      ? result.funding_observations.length : null,
    targetEvents: Array.isArray(result.target_events) ? result.target_events.length : null,
    latestTargetEvent: latestVerifiedTargetEvent(result.target_events),
  };
}
