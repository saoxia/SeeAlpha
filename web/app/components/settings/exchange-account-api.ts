export async function exchangeRequest<T>(path = "", init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/system/exchange-accounts${path}`, {
    ...init,
    credentials: "same-origin",
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (response.status === 204) return undefined as T;
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.detail || "交易所账户操作失败。");
  return payload as T;
}
