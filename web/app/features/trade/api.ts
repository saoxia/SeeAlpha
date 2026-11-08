import type { components } from "@/app/api/generated/schema";

export type TradingAccount = components["schemas"]["ExchangeAccountResponse"];
export type OrderRequest = components["schemas"]["ManualOrderRequest"];
export type Order = components["schemas"]["ManualOrderView"];
export type TradeState = components["schemas"]["ManualStateResponse"];
export type MarketSummary = components["schemas"]["ManualMarketSummary"];
export type Markets = components["schemas"]["ManualMarketsResponse"];
// Browser-owned public display state, independent of the HTTP response schema.
export type Market = {
  checked_at: number;
  symbol: string;
  environment: "production" | "sandbox";
  interval: string;
  candles: {time: number; open: string; high: string; low: string; close: string; volume: string}[];
  bids: string[][];
  asks: string[][];
  depth_time: number | null;
  trades: {id: number; price: string; qty: string; time: number; isBuyerMaker: boolean}[];
  last_price: string;
  change_percent: string;
  change_price?: string;
  base_volume?: string;
  open_interest?: string;
  open_interest_checked_at?: number;
  funding_interval_hours?: number;
  high: string;
  low: string;
  quote_volume: string;
  mark_price: string;
  index_price: string;
  funding_rate: string;
  next_funding_time: number;
};
export type Result = components["schemas"]["ManualOrderResponse"];

export async function tradingRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin", cache: "no-store",
    headers: { "Content-Type": "application/json", ...init?.headers },
    signal: init?.signal ?? AbortSignal.timeout(75_000) });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(body?.detail ?? `交易服务暂不可用（HTTP ${response.status}），请稍后核对。`);
  if (body === null) throw new Error("交易服务返回了无效响应，请刷新后核对状态。");
  return body as T;
}

export function isFresh(checkedAt: number | undefined, now = Date.now()): boolean {
  return checkedAt !== undefined && now >= checkedAt && now - checkedAt < 60_000;
}
export function orderBlockReason(input: {
  hasAccount: boolean; accountCheckedAt?: number; marketCheckedAt?: number; now: number;
  busy: boolean; pending: boolean; loading: boolean; accountError: string; marketError: string;
}): string {
  if (!input.hasAccount) return "请选择交易账户。";
  if (input.busy) return "正在提交，请等待结果。";
  if (input.pending) return "有操作结果待核实，请先核对交易结果。";
  if (input.accountError) return "账户查询失败，请刷新账户。";
  if (!isFresh(input.accountCheckedAt, input.now)) return input.loading ? "正在核对账户与委托…" : "账户快照已过期，请刷新账户。";
  if (input.marketError) return "行情连接异常，正在等待恢复。";
  if (!isFresh(input.marketCheckedAt, input.now)) return "正在等待新鲜的实时行情。";
  return "";
}

export function orderError(order: OrderRequest): string {
  if (!/^\d+(\.\d+)?$/.test(order.quantity) || Number(order.quantity) <= 0) return "请输入大于零的数量。";
  if (order.order_type === "limit" && (!order.price || !/^\d+(\.\d+)?$/.test(order.price) || Number(order.price) <= 0)) return "请输入有效委托价格。";
  if ((order.position_side === "LONG" || order.position_side === "SHORT") && order.reduce_only) return "双向持仓模式不能同时使用只减仓。";
  return "";
}

// getRandomValues also works on trusted LAN HTTP, where randomUUID is unavailable.
export function requestId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
