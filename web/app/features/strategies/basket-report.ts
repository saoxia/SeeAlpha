import type { BasketBacktestResult } from "./api";

export type BasketMetric = {
  label: string;
  value: string;
};

/** Only project values that actually exist in the Engine research report.
 * Never turn a simulated exposure into a claim about real account positions.
 */
export function basketReportMetrics(result: BasketBacktestResult): {
  positions: BasketMetric[];
  accounts: BasketMetric[];
  legDrift: string | null;
  fundingObservations: number | null;
  targetEvents: number | null;
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
  return {
    positions,
    accounts,
    legDrift: typeof last?.leg_drift === "string" ? last.leg_drift : null,
    fundingObservations: Array.isArray(result.funding_observations)
      ? result.funding_observations.length : null,
    targetEvents: Array.isArray(result.target_events) ? result.target_events.length : null,
  };
}
