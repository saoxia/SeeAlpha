import type { OperationPage } from "../live/operations";
import type {
  ExchangeAccount,
} from "@/app/components/types";
import type { DataFilters } from "./types";

export function timestamp(value: string): number | null {
  if (!value) return null;
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : null;
}

export async function apiError(response: Response, fallback: string) {
  const body = await response.json().catch(() => null) as { detail?: string } | null;
  return body?.detail || fallback;
}

export function buildQuery(filters: DataFilters, extra?: Record<string, string>) {
  const query = new URLSearchParams(extra);
  if (filters.accountId) query.set("account_id", filters.accountId);
  if (filters.eventType) query.set("event_type", filters.eventType);
  if (filters.symbol.trim()) query.set("symbol", filters.symbol.trim());
  const start = timestamp(filters.start);
  const end = timestamp(filters.end);
  if (start) query.set("start_ms", String(start));
  if (end) query.set("end_ms", String(end));
  return query;
}

async function getJson<T>(url: string, fallback: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { cache: "no-store", credentials: "same-origin", signal });
  if (!response.ok) throw new Error(await apiError(response, fallback));
  return response.json() as Promise<T>;
}

async function sendJson<T>(
  url: string,
  body: object,
  fallback: string,
): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await apiError(response, fallback));
  return response.json() as Promise<T>;
}

export type OperationBackfillResult = {
  records_scanned: number;
  records_added: number;
  warnings: string[];
};

export const dataManagementApi = {
  accounts: (signal?: AbortSignal) => getJson<ExchangeAccount[]>("/api/exchange-accounts", "无法读取账户列表。", signal),
  operations: (
    filters: DataFilters,
    extra: Record<string, string>,
    signal?: AbortSignal,
  ) => getJson<OperationPage>(
    `/api/operations?${buildQuery(filters, extra)}`,
    "无法读取操作记录。",
    signal,
  ),
  backfillOperations: (body: object) => sendJson<OperationBackfillResult>(
    "/api/operations/sync",
    body,
    "无法更新操作记录。",
  ),
};
