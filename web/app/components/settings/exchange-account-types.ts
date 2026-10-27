export type ExchangeId = "binance" | "okx";
export type ExchangeEnvironment = "production" | "sandbox";
export type MarketType = "spot" | "linear" | "option";

export type ExchangeAccount = {
  id: number;
  exchange: ExchangeId;
  account_name: string;
  environment: ExchangeEnvironment;
  key_hint: string;
  market_symbols: string[];
  last_test_status: "success" | "failed" | null;
  last_tested_at: number | null;
  created_at: number;
  updated_at: number;
};

export type ExchangeMarket = {
  symbol: string;
  market_type: MarketType;
  base: string;
  quote: string;
  expiry: number | null;
};

export type BalanceItem = {
  account: ExchangeAccount;
  balances: Record<string, string>;
  error: string | null;
};

export type BalanceResponse = {
  checked_at: number;
  items: BalanceItem[];
};
