import type { components } from "@/app/api/generated/schema";

export type DataMarket = components["schemas"]["DataMarket"];
export type DataCapabilities = components["schemas"]["DataCapabilities"];
export type DataJob = components["schemas"]["DataJobResponse"];
export type DataDeletionPlan = components["schemas"]["DataDeletionPlan"];
export type DataProduct = "futures_um" | "options";
export type DataItem = { symbol: string; product?: DataProduct; level: string; event_ranges_ns: string[][]; quality: string;
  contract?: { underlying?: string; expiry?: number; strike?: string; right?: string; multiplier?: string; quote?: string } };
export type DataInventory = { items: DataItem[] };
export type DataSelection = { symbol: string; start: string; end: string; intervals: string[]; product?: DataProduct };
export type DataPlan = {
  archive_days: number;
  missing_days: number;
  message: string;
  days: { day: string; archive_present: boolean; catalog_present: boolean; recorded_rows: number | null }[];
};
export type DataStatus = {
  storage: { available: boolean; writable: boolean; free_bytes: number | null; checked_at: string; message: string | null };
  tasks: DataJob[];
  monitor?: { state: string; checked_ms?: number; valid_until_ms?: number; reason?: string; configuration_error?: string | null };
};
export type FeedStatus = {
  state: string; data_ready: boolean; reason: string; checked_ms: number | null;
  valid_until_ms?: number | null;
  symbols?: Record<string, { next_ms?: number | null; last_websocket_ms: number | null; last_update_ms?: number | null }>;
};

export async function dataRequest<T>(path: string, body?: object, signal?: AbortSignal): Promise<T> {
  try {
    const response = await fetch(`/api/data/${path}`, {
      method: body ? "POST" : "GET", credentials: "same-origin", cache: "no-store", signal,
      ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
    });
    const result = await response.json().catch((error: unknown) => {
      if (signal?.aborted) throw signal.reason ?? error;
      return null;
    });
    if (!response.ok) throw new Error(typeof result?.detail === "string" ? result.detail : "数据操作失败，请稍后重试。");
    return result as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") {
      throw new DOMException("请求超时，请稍后重试。", "TimeoutError");
    }
    throw error;
  }
}

export function selection(symbol: string, start: string, end: string, level: string, product: DataProduct = "futures_um", recent = false): DataSelection {
  if (!symbol) throw new Error("请先选择 Binance 交易对。");
  if (product === "options" && recent && level === "tick") {
    const last = Date.now(), today = last - last % 86400000;
    return { symbol, product, start: new Date(Math.max(today, last - 1000)).toISOString(),
      end: new Date(last).toISOString(), intervals: [] };
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end)) throw new Error("请选择起止日期。");
  const first = new Date(`${start}T00:00:00Z`), last = new Date(`${end}T00:00:00Z`);
  const duration = last.getTime() - first.getTime();
  if (!Number.isFinite(duration) || duration <= 0) throw new Error("结束日期须晚于开始日期。");
  if (last.getTime() > Date.now() - Date.now() % 86400000 + (product === "options" ? 86400000 : 0)) throw new Error("只支持已完成的 UTC 日，结束日期最晚为今天。");
  return { symbol, start: first.toISOString(), end: last.toISOString(), intervals: level === "tick" ? [] : [level], ...(product === "options" ? { product } : {}) };
}

export const dataLevelLabels: Record<string, string> = { tick: "逐笔 Tick", "1s": "1 秒 K 线", "1m": "1 分钟 K 线", "5m": "5 分钟 K 线", "15m": "15 分钟 K 线", "3m": "3 分钟 K 线", "30m": "30 分钟 K 线", "1h": "1 小时 K 线", "2h": "2 小时 K 线", "4h": "4 小时 K 线", "6h": "6 小时 K 线", "8h": "8 小时 K 线", "12h": "12 小时 K 线", "1d": "日线", "3d": "3 日 K 线", "1w": "周线", "1M": "月线" };

export const dataJobLabels: Record<string, string> = { delete_pair: "删除交易对及全部行情", trim: "调整开始日期并删除旧数据", backfill: "历史补齐任务（兼容）", prepare: "获取行情数据", validate: "数据校验", materialize: "派生 K 线", export: "历史导出任务", delete: "删除历史数据" };
export const dataStateLabels: Record<string, string> = { running: "运行中", succeeded: "已完成", failed: "失败", cancelled: "已取消", interrupted: "已中断", unknown: "状态待确认" };

export function realtimeStatus(feed: FeedStatus | null, symbol: string, level: string, now: number, product: DataProduct = "futures_um"): string {
  if (product === "options") return "未接入";
  if (level !== "1m") return "未接入";
  if (!feed) return "状态未知";
  if (feed.reason === "not_started") return "未启动";
  if (feed.reason === "invalid_status") return "状态异常";
  if (feed.reason === "status_expired" || feed.valid_until_ms == null || now > feed.valid_until_ms) return "已过期";
  if (feed.symbols && Object.keys(feed.symbols).length && !feed.symbols[symbol]) return "未订阅";
  if (!feed.symbols?.[symbol]) return "待确认";
  if (feed.state === "READY" && feed.data_ready) return "运行中";
  return ({ WARMING_UP: "预热中", DEGRADED: "中断或降级", STOPPED: "已停止" }[feed.state] ?? "待确认");
}

// Task exclusion follows the backend's product/symbol scope, including orphaned work.
export function hasPairTask(tasks: Pick<DataJob, "status" | "parameters">[], symbol: string, product: DataProduct = "futures_um"): boolean {
  return tasks.some((job) => ["running", "unknown"].includes(job.status)
    && job.parameters.symbol === symbol && (job.parameters.product ?? "futures_um") === product);
}

export function taskAffects(job: Pick<DataJob, "action" | "parameters">, item: DataItem): boolean {
  if (job.parameters.symbol !== item.symbol || (job.parameters.product ?? "futures_um") !== (item.product ?? "futures_um")) return false;
  if (job.action === "delete_pair") return true;
  if (item.product === "options") {
    if (job.action === "delete") return true;
    return Array.isArray(job.parameters.intervals) && (item.level === "tick" ? job.parameters.intervals.length === 0 : job.parameters.intervals.includes(item.level));
  }
  if (job.action === "backfill") return item.level === job.parameters.base_level || Array.isArray(job.parameters.intervals) && job.parameters.intervals.includes(item.level);
  if (job.action === "delete" || job.action === "trim" || job.action === "validate") return true;
  if (item.level === "tick") return job.action === "prepare" || job.action === "materialize";
  return Array.isArray(job.parameters.intervals) && job.parameters.intervals.includes(item.level);
}

// Initial history and unchanged polling snapshots must not repeat operation results.
export function taskTransitions<T extends { id: string; status: string }>(previous: Map<string, string>, tasks: T[]): T[] {
  return tasks.filter((job) => previous.has(job.id) ? previous.get(job.id) !== job.status : job.status === "running");
}


export function managedItems(stored: DataItem[], feed: Pick<FeedStatus, "symbols"> | null, tasks: Pick<DataJob, "status" | "parameters">[]): DataItem[] {
  const items = new Map(stored.map((item) => [`${item.product ?? "futures_um"}:${item.symbol}:${item.level}`, item]));
  function include(symbol: string, level: string, product: DataProduct = "futures_um") {
    if (!(product === "options" ? /^[A-Z0-9]+-\d{6}-\d+(?:\.\d+)?-[CP]$/ : /^[A-Z0-9]+(?:USDT|USDC)$/).test(symbol)) return;
    const key = `${product}:${symbol}:${level}`;
    if (!items.has(key)) items.set(key, { symbol, level, ...(product === "options" ? { product } : {}), event_ranges_ns: [], quality: "unverified" });
  }
  Object.keys(feed?.symbols ?? {}).forEach((symbol) => include(symbol, "1m"));
  tasks.filter((job) => ["running", "unknown"].includes(job.status)).forEach((job) => {
    const symbol = String(job.parameters.symbol ?? "");
    const product = job.parameters.product === "options" ? "options" : "futures_um";
    if (product !== "options" || (Array.isArray(job.parameters.intervals) && job.parameters.intervals.length === 0)) include(symbol, "tick", product);
    if (Array.isArray(job.parameters.intervals)) job.parameters.intervals.forEach((level) => {
      if (typeof level === "string") include(symbol, level, product);
    });
  });
  return [...items.values()].sort((a, b) => a.symbol.localeCompare(b.symbol));
}


export const dataProductLabels: Record<DataProduct, string> = { futures_um: "U 本位永续", options: "期权" };
export function dataQuality(item: DataItem): string {
  if (!item.event_ranges_ns.length) return "—";
  if (item.quality === "partial_recent_trades" && item.product !== "options") return "近期逐笔 · 完整性待核实";
  return item.product === "options" ? item.level === "tick" ? "近期片段 · 不完整" : "交易所 K 线 · 待校验" : "待校验";
}

export type PairDeletePlanRequest = components["schemas"]["PairDeletePlanRequest"];
export type PairDeleteRequest = components["schemas"]["PairDeleteRequest"];

export type DataPolicy = Required<Pick<components["schemas"]["DataPolicy"], "symbol" | "product" | "base_level" | "intervals" | "realtime_enabled">> &
  Pick<components["schemas"]["DataPolicy"], "history_enabled" | "history_mode" | "history_start" | "history_days">;
export type BarUpdate = { symbol: string; interval: string; start_ms: number; end_ms: number;
  open: string; high: string; low: string; close: string; volume: string;
  is_closed: boolean; is_complete: boolean; source_levels?: string[]; revision: number; updated_ms: number; allow_new_risk: boolean };
export type ServiceObservation = { state: string; healthy: boolean; backlog: number | null; reason?: string | null; last_error?: string | null; last_processed_ms?: number | null; valid_until_ms?: number };
export type Subscription = { services?: Record<"collection" | "history" | "calculation", ServiceObservation>; policy: DataPolicy; runtime: FeedStatus & { job?: string | null; last_error?: string | null; gap_count?: number; bar_updates?: BarUpdate[] } };
export type Subscriptions = { items: Subscription[] };

export function subscriptionState(item: Subscription, now: number): string {
  if (!item.policy.realtime_enabled) return "仅历史数据";
  const runtime = item.runtime;
  if (runtime.reason === "process_exited") return "采集失败";
  if (runtime.reason === "invalid_status") return "状态异常";
  if (runtime.valid_until_ms == null) return "正在启动";
  if (now > runtime.valid_until_ms) return "已过期";
  if (runtime.state === "READY" && runtime.data_ready) return "运行中";
  return ({ WARMING_UP: "预热中", DEGRADED: "补缺或降级", STOPPED: "已停止" }[runtime.state] ?? "待确认");
}

export function groupDataItems(items: DataItem[], subscriptions: Subscription[]): (DataItem & { levels: string[] })[] {
  const groups = new Map<string, DataItem[]>();
  for (const item of items) {
    const key = `${item.product ?? "futures_um"}:${item.symbol}`;
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  for (const { policy } of subscriptions) {
    const key = `${policy.product}:${policy.symbol}`;
    if (!groups.has(key)) groups.set(key, [{ symbol: policy.symbol, product: policy.product,
      level: policy.base_level === "auto" ? policy.intervals[0] ?? "tick" : policy.base_level, event_ranges_ns: [], quality: "unverified" }]);
  }
  const order = ["tick", "1s", "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"];
  return [...groups.values()].map((values) => {
    const sorted = [...values].sort((a, b) => order.indexOf(a.level) - order.indexOf(b.level));
    const policy = subscriptions.find(({ policy }) => policy.symbol === sorted[0].symbol && policy.product === (sorted[0].product ?? "futures_um"))?.policy;
    const selected = policy?.base_level === "auto" ? sorted[0] : sorted.find((item) => item.level === policy?.base_level) ?? (policy ? {
      symbol: policy.symbol, product: policy.product, level: policy.base_level, event_ranges_ns: [], quality: "unverified",
    } : sorted[0]);
    return { ...selected, levels: [...new Set([...sorted.map((item) => item.level), ...(policy?.intervals ?? []), ...(policy && policy.base_level !== "auto" ? [policy.base_level] : [])])].sort((a, b) => order.indexOf(a) - order.indexOf(b)) };
  });
}

export type ConfigurationPlan = { policy: DataPolicy; requires_confirmation: boolean;
  deletion: (DataDeletionPlan & { retained_files?: { path: string; reason: string }[] }) | null };
