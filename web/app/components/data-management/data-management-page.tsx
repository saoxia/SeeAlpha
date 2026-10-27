"use client";

import { useCallback, useEffect, useState } from "react";
import type { ExchangeAccount, ExchangeTrade } from "../types";
import { OperationsList } from "./data-management-sections";
import { buildQuery, dataManagementApi, timestamp } from "@/app/features/data-management/api";
import { emptyDataFilters, type DataFilters, type DataTab } from "@/app/features/data-management/types";

export function DataManagementPage() {
  const [tab, setTab] = useState<DataTab>("operations");
  const [accounts, setAccounts] = useState<ExchangeAccount[]>([]);
  const [draftFilters, setDraftFilters] = useState<DataFilters>(emptyDataFilters);
  const [filters, setFilters] = useState<DataFilters>(emptyDataFilters);
  const [operations, setOperations] = useState<ExchangeTrade[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [backfillingOperations, setBackfillingOperations] = useState(false);
  const selectedAccount = accounts.find(
    (account) => String(account.id) === filters.accountId,
  );
  useEffect(() => {
    const controller = new AbortController();
    dataManagementApi.accounts(controller.signal)
      .then(setAccounts)
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setMessage(error instanceof Error ? error.message : "无法读取账户列表。");
      });
    return () => controller.abort();
  }, []);

  const loadData = useCallback(async (signal?: AbortSignal) => {
    if (tab === "export") {
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
      });
      await loadData();
      const warning = result.warnings.length
        ? `；${result.warnings.length} 项未完成，请检查交易所接口权限或时间范围`
        : "";
      setMessage(
        `操作记录更新完成：检查 ${result.records_scanned} 条，补齐 ${result.records_added} 条${warning}。`,
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法更新操作记录。");
    } finally {
      setBackfillingOperations(false);
    }
  }

  function exportData() {
    const query = buildQuery(filters);
    const anchor = document.createElement("a");
    anchor.href = `/api/operations/export?${query}`;
    anchor.download = "";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  }

  return (
    <section className="data-management-page">
      <header className="data-management-heading">
        <div>
          <span>DATA MANAGEMENT</span>
          <h1>数据管理</h1>
          <p>查询和导出账户操作记录。</p>
        </div>
        <div className="data-management-summary">
          <span>当前范围</span>
          <b>{selectedAccount?.account_name || "全部账户"}</b>
        </div>
      </header>

      <nav className="data-management-tabs" aria-label="数据管理分类">
        {([
          ["operations", "账户记录"],
          ["export", "数据导出"],
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
        {tab === "operations" || tab === "export" ? <>
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
        </> : null}
        <label><span>开始时间</span><input type="datetime-local" value={draftFilters.start} onChange={(event) => setDraftFilters((current) => ({ ...current, start: event.target.value }))} /></label>
        <label><span>结束时间</span><input type="datetime-local" value={draftFilters.end} onChange={(event) => setDraftFilters((current) => ({ ...current, end: event.target.value }))} /></label>
        <button type="submit">应用筛选</button>
      </form>

      {message && <p className="data-management-message">{message}</p>}

      <div className="data-management-content">
        {tab === "operations" && <div className="operation-backfill-bar">
          <div>
            <b>历史操作记录</b>
            <small>按当前账户与时间筛选检查缺失记录；未选择开始时间时默认检查最近 24 小时；历史覆盖可能不完整。</small>
          </div>
          <button
            type="button"
            disabled={backfillingOperations}
            onClick={() => void backfillOperations()}
          >
            {backfillingOperations ? "正在更新…" : "更新操作记录"}
          </button>
        </div>}
        {tab === "operations" && <OperationsList operations={operations} loading={loading} />}
        {tab === "operations" && hasMore && <button type="button" className="data-load-more" disabled={loading} onClick={() => void loadMoreOperations()}>加载更早记录</button>}
        {tab === "export" && <section className="data-export-card">
          <span>EXPORT</span>
          <h2>按当前筛选条件导出 CSV</h2>
          <p>文件使用 UTF-8 编码，可直接用 Excel 打开。导出不会修改任何数据。</p>
          <button type="button" onClick={exportData}>导出 CSV</button>
        </section>}
      </div>
    </section>
  );
}
