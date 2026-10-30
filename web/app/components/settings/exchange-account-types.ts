import type { components } from "@/app/api/generated/schema";

export type ExchangeId = "binance" | "okx";
export type ExchangeEnvironment = "production" | "sandbox";

export type ExchangeAccount = Omit<components["schemas"]["ExchangeAccountResponse"], "market_symbols" | "exchange" | "environment"> & {
  exchange: ExchangeId;
  environment: ExchangeEnvironment;
};

export type ExchangeAccountCreateRequest = components["schemas"]["ExchangeAccountCreateRequest"];
export type ExchangeAccountUpdateRequest = components["schemas"]["ExchangeAccountUpdateRequest"];

export type BalanceItem = {
  account: ExchangeAccount;
  balances: Record<string, string>;
  error: string | null;
};

export type BalanceResponse = {
  checked_at: number;
  items: BalanceItem[];
};
