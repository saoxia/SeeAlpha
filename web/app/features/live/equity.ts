import type { components } from "../../api/generated/schema.ts";

export type EquityRebuild = components["schemas"]["EquityRebuildResponse"];

export async function requestEquityRebuild(accountId: number, signal: AbortSignal, jobId?: string): Promise<EquityRebuild> {
  const path = `/api/exchange-accounts/${accountId}/equity-rebuilds${jobId ? `/${encodeURIComponent(jobId)}` : ""}`;
  const response = await fetch(path, {
    method: jobId ? "GET" : "POST",
    cache: "no-store",
    credentials: "same-origin",
    signal,
    ...(!jobId ? { headers: { "Content-Type": "application/json" }, body: "{}" } : {}),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(body?.detail || "资产重算请求失败。");
  }
  return response.json() as Promise<EquityRebuild>;
}
