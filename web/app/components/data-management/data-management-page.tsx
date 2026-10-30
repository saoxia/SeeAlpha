"use client";

import { useCallback, useEffect, useState } from "react";
import type { ExchangeAccount, ExchangeTrade } from "../types";
import { MarketDataPanel } from "./market-data-panel";
import { OperationsList } from "./data-management-sections";
import { dataManagementApi, timestamp } from "@/app/features/data-management/api";
import { emptyDataFilters, type DataFilters, type DataTab } from "@/app/features/data-management/types";

export function DataManagementPage() {
  const [tab, setTab] = useState<DataTab>("market");
  const [accounts, setAccounts] = useState<ExchangeAccount[]>([]);
  const [draftFilters, setDraftFilters] = useState<DataFilters>(emptyDataFilters);
  const [filters, setFilters] = useState<DataFilters>(emptyDataFilters);
  const [operations, setOperations] = useState<ExchangeTrade[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [backfillingOperations, setBackfillingOperations] = useState(false);
  useEffect(() => {
    if (tab !== "operations") return;
    const controller = new AbortController();
    dataManagementApi.accounts(controller.signal)
      .then(setAccounts)
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setMessage(error instanceof Error ? error.message : "无法读取账户列表。");
      });
    return () => controller.abort();
  }, [tab]);

  const loadData = useCallback(async (signal?: AbortSignal) => {
    if (tab !== "operations") {
      setLoading(false);
      return;
    }
    setLoading(true);
    setMessage("");
    try {
      const page = await dataManagementApi.operations(filters, { limit: "100" }, signal);
      setOperations(page.records);
      setNextBefore(page.next_cursor);
      setHasMore(page.has_more);
      setMessage(page.warnings.join("；"));
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setMessage(error instanceof Error ? error.message : "数据加载失败。");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [filters, tab]);

  useEffect(() => {
    const controller = new AbortController();
    const initialLoad = window.setTimeout(() => void loadData(controller.signal), 0);
    return () => {
      controller.abort();
      window.clearTimeout(initialLoad);
    };
  }, [loadData]);

  async function loadMoreOperations() {
    if (!nextBefore || !hasMore || loading) return;
    setLoading(true);
    try {
      const page = await dataManagementApi.operations(filters, {
        limit: "100",
        cursor: nextBefore,
      });
      setOperations((current) => [...current, ...page.records]);
      setNextBefore(page.next_cursor);
      setHasMore(page.has_more);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法读取更多记录。");
    } finally {
      setLoading(false);
    }
  }

  async function backfillOperations() {
    setBackfillingOperations(true);
    setMessage("");
    try {
      const result = await dataManagementApi.backfillOperations({
        account_id: filters.accountId ? Number(filters.accountId) : null,
        start_ms: timestamp(filters.start),
        end_ms: timestamp(filters.end),
        cashflows_only: filters.eventType === "cashflow",
      });
      await loadData();
      const warning = result.warnings.length
        ? `；${result.warnings.length} 项未完成，请检查交易所接口权限或时间范围`
        : "";
      setMessage(
        `账户记录更新完成：检查 ${result.records_scanned} 条，补齐 ${result.records_added} 条${warning}。`,
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法更新操作记录。");
    } finally {
      setBackfillingOperations(false);
    }
  }

  return (
    <section className="data-management-page">
      <nav className="data-management-tabs" aria-label="数据管理分类">
        {([
          ["market", "行情数据"],
          ["operations", "账户记录"],
        ] as Array<[DataTab, string]>).map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={tab === id ? "active" : ""}
            onClick={() => {
              setTab(id);
            }}
          >
            {label}
          </button>
        ))}
      </nav>

      {tab === "market" && <MarketDataPanel />}
      <div hidden={tab === "market"}>
      <form
        className="data-filter-bar"
        onSubmit={(event) => {
          event.preventDefault();
          setFilters(draftFilters);
        }}
      >
        <label>
          <span>账户</span>
          <select
            value={draftFilters.accountId}
            onChange={(event) => setDraftFilters((current) => ({ ...current, accountId: event.target.value }))}
          >
            <option value="">全部账户</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.account_name} · {account.exchange.toUpperCase()}
              </option>
            ))}
          </select>
        </label>
          <label>
            <span>类型</span>
            <select
              value={draftFilters.eventType}
              onChange={(event) => setDraftFilters((current) => ({ ...current, eventType: event.target.value }))}
            >
              <option value="">全部类型</option>
              <option value="place">下单</option>
              <option value="amend">改单</option>
              <option value="cancel">撤单</option>
              <option value="fill">成交</option>
              <option value="reject">拒绝</option>
              <option value="expire">过期</option>
              <option value="correction">对账修正</option>
              <option value="cashflow">资金变动</option>
            </select>
          </label>
          <label>
            <span>交易对</span>
            <input
              value={draftFilters.symbol}
              placeholder="BTC/USDT"
              onChange={(event) => setDraftFilters((current) => ({ ...current, symbol: event.target.value }))}
            />
          </label>
        <label><span>开始时间</span><input type="datetime-local" value={draftFilters.start} onChange={(event) => setDraftFilters((current) => ({ ...current, start: event.target.value }))} /></label>
        <label><span>结束时间</span><input type="datetime-local" value={draftFilters.end} onChange={(event) => setDraftFilters((current) => ({ ...current, end: event.target.value }))} /></label>
        <button type="submit">应用筛选</button>
      </form>

      {message && <p className="data-management-message">{message}</p>}

      <div className="data-management-content">
        {tab === "operations" && <div className="operation-backfill-bar">
          <div>
            <b>历史账户记录</b>
            <small>按当前账户与时间筛选补齐记录。未选择开始时间时，订单检查最近 24 小时，资金首次检查最近 87 天、之后续传；历史覆盖可能不完整。</small>
          </div>
          <button
            type="button"
            disabled={backfillingOperations}
            onClick={() => void backfillOperations()}
          >
            {backfillingOperations ? "正在更新…" : "更新账户记录"}
          </button>
        </div>}
        {tab === "operations" && <OperationsList operations={operations} loading={loading} />}
        {tab === "operations" && hasMore && <button type="button" className="data-load-more" disabled={loading} onClick={() => void loadMoreOperations()}>加载更早记录</button>}

      </div>
      </div>
    </section>
  );
}
