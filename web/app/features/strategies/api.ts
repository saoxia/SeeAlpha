import { errorDetail } from "../identity/model";

export type StrategyVersion = {
  id: number;
  version: string;
  parameters: Record<string, unknown>;
  grants: Array<"development" | "production">;
  created_at: number;
};

export type StrategyPackage = {
  id: number;
  code_key: string;
  name: string;
  description: string | null;
  owner_id: number | null;
  visibility: "system" | "private" | "public";
  versions: StrategyVersion[];
};

export type BasketMode = "funding_carry" | "protected_options";
export type BasketBacktestResult = {
  mode: string;
  bar_count: number;
  starting_balance: string;
  ending_balance: string;
  fills: Array<{
    instrument_id: string; price: string; quantity: string;
    phase?: string; commission?: string; currency?: string;
    client_order_id?: string;
  }>;
  equity_curve: Array<{
    ts_ns: number;
    total_equity: string;
    spot_quantity?: string;
    perpetual_quantity?: string;
    put_quantity?: string;
    call_quantity?: string;
    leg_drift?: string;
    spot_equity?: string;
    perpetual_equity?: string;
    options_equity?: string;
  }>;
  [key: string]: unknown;
};

export type IssuedStrategyLoad = {
  id: number;
  account_id: number | string;
  code_key: string;
  purpose: "development" | "production";
  parameters: Record<string, unknown>;
};

export type ShareRecipient = { id: number; username: string };
export type TenantAccount = { id: number; kind: string; account_name: string };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  const response = await fetch(path, {
    ...init, credentials: "same-origin", cache: "no-store", headers,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(errorDetail(payload, "策略请求失败。"));
  return payload as T;
}

export function listStrategyPackages() {
  return request<StrategyPackage[]>("/api/strategy-packages");
}

export async function zipToBase64(file: File): Promise<string> {
  if (!file.name.toLowerCase().endsWith(".zip")) throw new Error("请选择 ZIP 格式的策略包。");
  if (!file.size || file.size > 290_000) throw new Error("策略 ZIP 须小于 290 KB。");
  const bytes = new Uint8Array(await file.arrayBuffer());
  let raw = "";
  for (let i = 0; i < bytes.length; i += 8192) {
    raw += String.fromCharCode(...bytes.subarray(i, i + 8192));
  }
  return btoa(raw);
}

export async function uploadStrategyPackage(file: File, packageId?: number) {
  const archive = await zipToBase64(file);
  const path = packageId === undefined
    ? "/api/strategy-packages"
    : `/api/strategy-packages/${packageId}/versions`;
  return request<unknown>(path, { method: "POST", body: JSON.stringify({ archive }) });
}

export function cloneStrategyPackage(packageId: number) {
  return request<StrategyPackage>(`/api/strategy-packages/${packageId}/clone`, {
    method: "POST", body: "{}",
  });
}

export function setStrategyVisibility(packageId: number, visibility: "private" | "public") {
  return request<StrategyPackage>(`/api/strategy-packages/${packageId}/visibility`, {
    method: "PUT", body: JSON.stringify({ visibility }),
  });
}

export function listStrategyShares(packageId: number) {
  return request<ShareRecipient[]>(`/api/strategy-packages/${packageId}/shares`);
}

export function shareStrategyPackage(packageId: number, username: string) {
  return request<ShareRecipient[]>(`/api/strategy-packages/${packageId}/shares`, {
    method: "POST", body: JSON.stringify({ username }),
  });
}

export function unshareStrategyPackage(packageId: number, userId: number) {
  return request<ShareRecipient[]>(`/api/strategy-packages/${packageId}/shares/${userId}`, {
    method: "DELETE",
  });
}

export function strategyArchiveUrl(packageId: number, versionId: number) {
  return `/api/strategy-packages/${packageId}/versions/${versionId}/archive`;
}

export function listIssuedStrategyLoads(tenantId: number) {
  return request<IssuedStrategyLoad[]>(`/api/tenants/${tenantId}/strategy-loads`);
}

export function listTenantAccounts(tenantId: number) {
  return request<TenantAccount[]>(`/api/tenants/${tenantId}/accounts`);
}

export function issueStrategy(
  tenantId: number, versionId: number, accountId: number,
  parameters?: Record<string, unknown>,
) {
  return request<unknown>(`/api/tenants/${tenantId}/strategy-loads`, {
    method: "POST", body: JSON.stringify({
      version_id: versionId, account_id: accountId,
      ...(parameters ? { parameters } : {}),
    }),
  });
}

export function runIssuedSpotPerpBacktest(
  tenantId: number, accountId: number, values: {
    issuance_id: string; start: string; end: string; interval: "1m" | "5m";
    starting_spot_balance: string; starting_perpetual_balance: string;
    max_gross_notional: string;
  },
) {
  return request<BasketBacktestResult>(
    `/api/tenants/${tenantId}/accounts/${accountId}/issued-spot-perp-backtests`, {
      method: "POST", body: JSON.stringify(values),
    },
  );
}

export function latestIssuedSpotPerpBacktest(tenantId: number, accountId: number) {
  return request<BasketBacktestResult>(
    `/api/tenants/${tenantId}/accounts/${accountId}/issued-spot-perp-backtests/latest`,
  );
}

export function runBasketBacktest(
  tenantId: number, accountId: number, values: {
    mode: BasketMode; start: string; end: string; interval: "1m" | "5m";
    starting_primary_balance: string; starting_perpetual_balance: string;
    max_gross_notional: string;
  },
) {
  return request<BasketBacktestResult>(`/api/tenants/${tenantId}/accounts/${accountId}/basket-backtests`, {
    method: "POST", body: JSON.stringify(values),
  });
}

export function latestBasketBacktest(tenantId: number, accountId: number, mode: BasketMode) {
  return request<BasketBacktestResult>(
    `/api/tenants/${tenantId}/accounts/${accountId}/basket-backtests/${mode}/latest`,
  );
}
