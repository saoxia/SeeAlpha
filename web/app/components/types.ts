import type { components } from "@/app/api/generated/schema";

export type AppView = "live" | "trade" | "data" | "research" | "settings" | "tenants" | "users";

export type ExchangeId = "binance" | "okx";

type ApiSchema<Name extends keyof components["schemas"]> = Required<components["schemas"][Name]>;

export type ExchangeAccount = Omit<ApiSchema<"ExchangeAccountNavigationResponse">, "exchange"> & {
  exchange: ExchangeId;
};

export type ExchangeAccountSummary = ApiSchema<"ExchangeAccountSummaryResponse">;

export type ExchangeTrade = Omit<ApiSchema<"ExchangeTradeResponse">, "exchange" | "event_type" | "source"> & {
  exchange: ExchangeId;
  event_type: "place" | "amend" | "cancel" | "fill" | "reject" | "expire" | "correction" | "cashflow";
  source: string;
  strategy_id?: string | null;
  environment?: "production" | "sandbox";
  confirmed?: boolean;
  client_order_id?: string | null;
  trade_id?: string | null;
  asset?: string | null;
  income_type?: string | null;
  transaction_id?: string | null;
  info?: string | null;
};

export type EquityCandle = ApiSchema<"EquityCandleResponse">;

export type EquityCandleSeries = Omit<ApiSchema<"EquityCandleSeriesResponse">, "metric" | "candles"> & {
  metric: "total_equity";
  is_sample: boolean;
  candles: EquityCandle[];
};

export type ExchangePosition = Omit<ApiSchema<"ExchangePositionResponse">, "exchange"> & {
  exchange: ExchangeId;
};

export type ExchangePortfolio = Omit<
  ApiSchema<"ExchangePortfolioResponse">,
  "account" | "trades" | "positions" | "errors" | "warnings"
> & {
  account: ExchangeAccountSummary;
  trades: ExchangeTrade[];
  positions: ExchangePosition[];
  errors: Partial<Record<"account" | "trades" | "positions", string>>;
  warnings: Partial<Record<"account" | "trades" | "positions", string>>;
};
