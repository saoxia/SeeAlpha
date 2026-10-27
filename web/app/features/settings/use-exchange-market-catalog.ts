"use client";

import { useEffect, useState } from "react";
import { exchangeMarketRequest } from "@/app/components/settings/exchange-account-api";
import type {
  ExchangeEnvironment,
  ExchangeId,
  ExchangeMarket,
  MarketType,
} from "@/app/components/settings/exchange-account-types";

type CatalogOptions = {
  enabled: boolean;
  exchange: ExchangeId;
  environment: ExchangeEnvironment;
  marketType: MarketType;
  search: string;
};

export function useExchangeMarketCatalog({
  enabled,
  exchange,
  environment,
  marketType,
  search,
}: CatalogOptions) {
  const [markets, setMarkets] = useState<ExchangeMarket[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError("");
      const query = new URLSearchParams({
        exchange,
        environment,
        market_type: marketType,
        search: search.trim(),
      });
      exchangeMarketRequest<ExchangeMarket[]>(query, controller.signal)
        .then(setMarkets)
        .catch((requestError: unknown) => {
          if (requestError instanceof DOMException && requestError.name === "AbortError") return;
          setError(requestError instanceof Error
            ? requestError.message
            : "无法读取交易所市场列表。");
        })
        .finally(() => setLoading(false));
    }, 220);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [enabled, environment, exchange, marketType, search]);

  return { markets, loading, error };
}
