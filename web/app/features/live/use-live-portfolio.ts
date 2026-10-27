"use client";

import { useCallback, useEffect, useState } from "react";
import type { ExchangeAccount, ExchangePortfolio } from "@/app/components/types";
import { fetchAccountNavigation, fetchPortfolio } from "./api";

export type SelectedVenue = "overview" | number;

export function useLivePortfolio() {
  const [accounts, setAccounts] = useState<ExchangeAccount[]>([]);
  const [venue, setVenue] = useState<SelectedVenue>("overview");
  const [portfolio, setPortfolio] = useState<ExchangePortfolio | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [overviewEquity, setOverviewEquity] = useState<string | null>(null);
  const [accountEquities, setAccountEquities] = useState<Record<number, string | null>>({});

  useEffect(() => {
    const controller = new AbortController();
    fetchAccountNavigation(controller.signal)
      .then((items) => {
        setAccounts(items);
        setVenue((current) => (
          current === "overview" || items.some((account) => account.id === current)
            ? current
            : "overview"
        ));
      })
      .catch((requestError: unknown) => {
        if (requestError instanceof DOMException && requestError.name === "AbortError") return;
        setAccounts([]);
        setVenue("overview");
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!accounts.length) return;
    const controller = new AbortController();
    Promise.all(accounts.map(async (account) => {
      try {
        const item = await fetchPortfolio(account.id, controller.signal);
        return [account.id, item.account.total_equity] as const;
      } catch {
        return [account.id, null] as const;
      }
    }))
      .then((entries) => setAccountEquities(Object.fromEntries(entries)))
      .catch(() => undefined);
    return () => controller.abort();
  }, [accounts]);

  useEffect(() => {
    const controller = new AbortController();
    fetchPortfolio(venue === "overview" ? null : venue, controller.signal)
      .then((nextPortfolio) => {
        setPortfolio(nextPortfolio);
        if (venue === "overview") {
          setOverviewEquity(nextPortfolio.account.total_equity);
        } else {
          setAccountEquities((current) => ({
            ...current,
            [venue]: nextPortfolio.account.total_equity,
          }));
        }
      })
      .catch((requestError: unknown) => {
        if (requestError instanceof DOMException && requestError.name === "AbortError") return;
        setError(requestError instanceof Error ? requestError.message : "无法读取账户数据。");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [venue]);

  const selectVenue = useCallback((nextVenue: SelectedVenue) => {
    setVenue((current) => {
      if (current === nextVenue) return current;
      setPortfolio(null);
      setLoading(true);
      setError("");
      return nextVenue;
    });
  }, []);

  return {
    accounts,
    venue,
    portfolio,
    loading,
    error,
    overviewEquity,
    accountEquities,
    selectVenue,
  };
}
