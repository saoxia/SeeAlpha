import type { ExchangeAccount, ExchangePortfolio } from "@/app/components/types";

const requestOptions = (signal: AbortSignal): RequestInit => ({
  cache: "no-store",
  credentials: "same-origin",
  signal,
});

export async function fetchAccountNavigation(signal: AbortSignal) {
  const response = await fetch("/api/exchange-accounts", requestOptions(signal));
  if (!response.ok) throw new Error("无法读取交易所账户。");
  return response.json() as Promise<ExchangeAccount[]>;
}

export async function fetchPortfolio(
  accountId: number | null,
  signal: AbortSignal,
  refresh = false,
) {
  const path = accountId === null
    ? "/api/exchange-accounts/portfolio"
    : `/api/exchange-accounts/${accountId}/portfolio`;
  const response = await fetch(`${path}${refresh ? "?refresh=true" : ""}`, requestOptions(signal));
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(payload?.detail || "无法读取账户数据。");
  }
  return response.json() as Promise<ExchangePortfolio>;
}
