"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import type {
  ExchangeAccount,
  ExchangeTrade,
} from "../types";
import { operationLabels, operationSourceLabel, operationsPath, mergeLatestOperations, type OperationPage } from "@/app/features/live/operations";
import { decimal } from "./portfolio-panel-shared";

const TRADE_PAGE_SIZE = 50;


function sideLabel(side: string) {
  if (side === "buy") return "买入";
  if (side === "sell") return "卖出";
  return side || "--";
}

function chronological(trades: ExchangeTrade[]): ExchangeTrade[] {
  return [...trades].sort(
    (left, right) => Number(left.timestamp ?? 0) - Number(right.timestamp ?? 0),
  );
}


type TradesPanelProps = {
  account: ExchangeAccount | null;
  overview: boolean;
  scopeName: string | undefined;
  scrollContainerRef: RefObject<HTMLDivElement | null>;
};

type ScrollAnchor = {
  height: number;
  top: number;
};

export function TradesPanel({
  account,
  overview,
  scopeName,
  scrollContainerRef,
}: TradesPanelProps) {
  const [trades, setTrades] = useState<ExchangeTrade[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingLatest, setLoadingLatest] = useState(true);
  const [latestError, setLatestError] = useState("");
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [paginationError, setPaginationError] = useState("");
  const [paginationWarning, setPaginationWarning] = useState("");
  const loadingOlderRef = useRef(false);
  const scrollToBottomRef = useRef(true);
  const scrollAnchorRef = useRef<ScrollAnchor | null>(null);

  useEffect(() => {
    if (!overview && !account) return;
    const controller = new AbortController();
    const accountId = overview ? null : account!.id;
    const loadLatest = async () => {
      const container = scrollContainerRef.current;
      const shouldStickToBottom = !container
        || container.scrollHeight - container.scrollTop - container.clientHeight <= 24;
      try {
        const response = await fetch(operationsPath(accountId, null, TRADE_PAGE_SIZE), {
          cache: "no-store",
          credentials: "same-origin",
          signal: controller.signal,
        });
        const payload = await response.json().catch(() => null) as
          | OperationPage
          | { detail?: string }
          | null;
        if (!response.ok) {
          throw new Error(payload && "detail" in payload ? payload.detail : "无法读取操作记录。");
        }
        const page = payload as OperationPage;
        setTrades((current) => mergeLatestOperations(current, page));
        setNextBefore((current) => current ?? page.next_cursor);
        setHasMore((current) => current || Boolean(page.has_more && page.next_cursor));
        setPaginationWarning(page.warnings.join("；"));
        setLatestError("");
        scrollToBottomRef.current = shouldStickToBottom;
      } catch (requestError) {
        if (requestError instanceof DOMException && requestError.name === "AbortError") return;
        setLatestError(requestError instanceof Error ? requestError.message : "无法读取操作记录。");
      } finally {
        if (!controller.signal.aborted) setLoadingLatest(false);
      }
    };
    void loadLatest();
    const timer = window.setInterval(() => void loadLatest(), 60_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [account, overview, scrollContainerRef]);

  useLayoutEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    const anchor = scrollAnchorRef.current;
    if (anchor) {
      container.scrollTop = container.scrollHeight - anchor.height + anchor.top;
      scrollAnchorRef.current = null;
    } else if (scrollToBottomRef.current) {
      container.scrollTop = container.scrollHeight;
      scrollToBottomRef.current = false;
    }
  }, [scrollContainerRef, trades]);

  const loadOlder = useCallback(async () => {
    const container = scrollContainerRef.current;
    if (
      !container
      || !hasMore
      || !nextBefore
      || loadingOlderRef.current
      || (!overview && !account)
    ) return;

    loadingOlderRef.current = true;
    setLoadingOlder(true);
    setPaginationError("");
    const anchor = { height: container.scrollHeight, top: container.scrollTop };
    const accountId = overview ? null : account!.id;
    try {
      const response = await fetch(
        operationsPath(accountId, nextBefore, TRADE_PAGE_SIZE),
        { cache: "no-store", credentials: "same-origin" },
      );
      const payload = await response.json().catch(() => null) as
        | OperationPage
        | { detail?: string }
        | null;
      if (!response.ok) {
        throw new Error(payload && "detail" in payload ? payload.detail : "无法读取更早的操作记录。");
      }
      const page = payload as OperationPage;

      scrollAnchorRef.current = anchor;
      setTrades((current) => {
        const merged = new Map<string, ExchangeTrade>();
        for (const trade of [...page.records, ...current]) {
          merged.set(trade.id, trade);
        }
        return chronological([...merged.values()]);
      });
      setNextBefore(page.next_cursor);
      setHasMore(Boolean(page.has_more && page.next_cursor && page.records.length));
      setPaginationWarning(page.warnings.join("；"));
    } catch (requestError) {
      scrollAnchorRef.current = null;
      setPaginationError(
        requestError instanceof Error ? requestError.message : "无法读取更早的操作记录。",
      );
    } finally {
      loadingOlderRef.current = false;
      setLoadingOlder(false);
    }
  }, [account, hasMore, nextBefore, overview, scrollContainerRef]);

  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    const handleScroll = () => {
      if (container.scrollTop <= 8) void loadOlder();
    };
    container.addEventListener("scroll", handleScroll, { passive: true });
    return () => container.removeEventListener("scroll", handleScroll);
  }, [loadOlder, scrollContainerRef]);

  return (
    <>
      <h2>{scopeName ? `${scopeName} · 操作记录` : "操作记录"}</h2>
      {!account && !overview && <p className="portfolio-state">请选择一个账户查看操作记录。</p>}
      {loadingLatest && <p className="portfolio-state">正在读取项目操作记录…</p>}
      {!loadingLatest && latestError && <p className="portfolio-state error">{latestError}</p>}
      {!loadingLatest && !latestError && (overview || account) && <>
            {loadingOlder && <p className="operation-page-state">正在加载更早的操作记录…</p>}
            {paginationError && <button type="button" className="operation-page-state error" onClick={() => void loadOlder()}>{paginationError} · 点击重试</button>}
            {paginationWarning && <p className="portfolio-warning">记录覆盖说明：{paginationWarning}</p>}
            {!hasMore && trades.length > 0 && <p className="operation-page-state">没有更早的操作记录</p>}
            {trades.length
              ? trades.map((trade) => (
                  <div className="trade-row portfolio-trade" key={trade.id}>
                    <span>
                      <b>{trade.symbol} · {operationLabels[trade.event_type] ?? "操作"} · {sideLabel(trade.side)}</b>
                      <small>{operationSourceLabel(trade)}</small>
                      <small>{trade.timestamp ? new Date(trade.timestamp).toLocaleString("zh-CN", { hour12: false }) : "时间未知"}</small>
                      {overview && <small className="portfolio-source">{trade.account_name} · {trade.exchange.toUpperCase()}</small>}
                    </span>
                    <span>
                      <b>{decimal(trade.amount)} @ {decimal(trade.price)}</b>
                      <small>{trade.event_type === "fill"
                        ? `成交额 ${decimal(trade.cost)}${trade.fee_cost ? ` · 手续费 ${decimal(trade.fee_cost)} ${trade.fee_currency}` : ""}`
                        : `状态 ${(trade.status || "--").toUpperCase()}${trade.filled ? ` · 已成交 ${decimal(trade.filled)}` : ""}`}</small>
                    </span>
                  </div>
                ))
              : <p className="portfolio-state">当前没有可显示的操作记录。</p>}
          </>}
    </>
  );
}
