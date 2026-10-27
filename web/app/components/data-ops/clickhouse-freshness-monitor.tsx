"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type FreshnessItem = {
  table: string;
  time_column: string | null;
  latest_epoch: number | null;
  lag_seconds: number | null;
  healthy: boolean;
  message: string | null;
};

type FreshnessResponse = {
  refresh_interval_seconds: number;
  tables: string[];
  updated_at: number | null;
  checked_at: number | null;
  items: FreshnessItem[];
};

type AvailableTable = {
  name: string;
  time_column: string | null;
};

async function clickhouseRequest(path: string, init?: RequestInit) {
  const response = await fetch(`/api/clickhouse/${path}`, {
    ...init,
    credentials: "same-origin",
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.detail || "ClickHouse 监控请求失败。");
  return payload;
}

export function ClickhouseFreshnessMonitor() {
  const [status, setStatus] = useState<FreshnessResponse | null>(null);
  const [availableTables, setAvailableTables] = useState<AvailableTable[]>([]);
  const [selectedTable, setSelectedTable] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [removingTable, setRemovingTable] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [error, setError] = useState("");
  const [addError, setAddError] = useState("");

  const loadStatus = useCallback(async (signal?: AbortSignal) => {
    const result = await clickhouseRequest("monitor/status", { signal }) as FreshnessResponse;
    setStatus(result);
    setError("");
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const initialLoad = window.setTimeout(() => {
      loadStatus(controller.signal)
        .catch((requestError: unknown) => {
          if (requestError instanceof DOMException && requestError.name === "AbortError") return;
          setError(requestError instanceof Error ? requestError.message : "无法读取数据水位缓存。");
        })
        .finally(() => setLoading(false));
    }, 0);
    const timer = window.setInterval(() => {
      loadStatus().catch(() => undefined);
    }, 60_000);
    return () => {
      controller.abort();
      window.clearTimeout(initialLoad);
      window.clearInterval(timer);
    };
  }, [loadStatus]);

  const addableTables = useMemo(
    () => availableTables.filter((table) => !status?.tables.includes(table.name)),
    [availableTables, status?.tables],
  );

  async function refreshNow() {
    setRefreshing(true);
    setError("");
    try {
      const result = await clickhouseRequest("monitor/refresh", { method: "POST" }) as FreshnessResponse;
      setStatus(result);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "数据水位刷新失败。");
    } finally {
      setRefreshing(false);
    }
  }

  async function openAddMonitor() {
    setAddOpen(true);
    setAddError("");
    setSelectedTable("");
    try {
      const tables = await clickhouseRequest("tables") as AvailableTable[];
      setAvailableTables(tables);
    } catch (requestError) {
      setAddError(requestError instanceof Error ? requestError.message : "无法读取 ClickHouse 表。");
    }
  }

  async function addMonitor() {
    if (!selectedTable) return;
    setAdding(true);
    setAddError("");
    try {
      const result = await clickhouseRequest("monitor/tables", {
        method: "POST",
        body: JSON.stringify({ table: selectedTable }),
      }) as FreshnessResponse;
      setStatus(result);
      setAddOpen(false);
    } catch (requestError) {
      setAddError(requestError instanceof Error ? requestError.message : "添加 OLAP 监控失败。");
    } finally {
      setAdding(false);
    }
  }

  async function removeMonitor(table: string) {
    setRemovingTable(table);
    setError("");
    try {
      const result = await clickhouseRequest(`monitor/tables/${encodeURIComponent(table)}`, {
        method: "DELETE",
      }) as FreshnessResponse;
      setStatus(result);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "删除 OLAP 监控失败。");
    } finally {
      setRemovingTable(null);
    }
  }

  return (
    <>
      <section className="clickhouse-monitor">
        <div className="monitor-list-head clickhouse-monitor-head">
          <div><span>01</span><h2>OLAP数据健康</h2></div>
          <div className="monitor-list-head-actions">
            <small>{loading ? "正在读取缓存…" : "每 1 分钟后台更新 · UTC"}</small>
            <button className="monitor-icon-button" disabled={refreshing} type="button" aria-label="立即刷新 OLAP 数据健康" title="立即刷新" onClick={() => void refreshNow()}>
              <RefreshIcon spinning={refreshing} />
            </button>
            <button className="monitor-add-button" type="button" onClick={() => void openAddMonitor()}>+ 添加监控</button>
            <button className="monitor-settings-button" type="button" onClick={() => setSettingsOpen(true)}>数据监控设置</button>
          </div>
        </div>

        {error && <p className="clickhouse-monitor-error">{error}</p>}
        {!error && !loading && status?.items.length === 0 && (
          <p className="monitor-empty">{status.tables.length > 0
            ? "监控项已保存，正在等待后台首次检查；也可以点击刷新图标立即查询。"
            : "还没有监控项，请点击右上角“添加监控”。后台会每分钟更新一次数据。"}</p>
        )}
        {status?.items.map((item) => (
          <article className="clickhouse-table-row" key={item.table}>
            <i className={`monitor-dot ${item.healthy ? "healthy" : "fault"}`} aria-hidden="true" />
            <div className="clickhouse-table-name"><b>{item.table}</b><span>{item.time_column ? `时间字段：${item.time_column}` : "未识别时间字段"}</span></div>
            <div><small>最新数据时间（UTC）</small><b>{formatUtc(item.latest_epoch)}</b></div>
            <div><small>数据延迟</small><b>{formatLag(item.lag_seconds)}</b></div>
            <div><small>健康状态</small><b className={item.healthy ? "freshness-healthy" : "freshness-fault"}>{item.healthy ? "健康" : "异常"}</b></div>
            <div className="monitor-actions clickhouse-row-actions">
              <button className="remove" disabled={removingTable === item.table} type="button" onClick={() => void removeMonitor(item.table)}>{removingTable === item.table ? "删除中" : "删除"}</button>
            </div>
            {item.message && <p>{item.message}</p>}
          </article>
        ))}
        {status?.checked_at && <div className="clickhouse-checked-at">缓存更新：{formatUtc(status.checked_at)}</div>}
      </section>

      {addOpen && (
        <div className="monitor-modal-backdrop">
          <button className="monitor-modal-dismiss" type="button" aria-label="关闭添加 OLAP 监控弹窗" onClick={() => setAddOpen(false)} />
          <section className="monitor-modal clickhouse-add-modal" role="dialog" aria-modal="true" aria-labelledby="clickhouse-add-title">
            <div className="monitor-modal-head">
              <div><span>NEW DATA MONITOR</span><h2 id="clickhouse-add-title">添加监控</h2></div>
              <button type="button" aria-label="关闭" onClick={() => setAddOpen(false)}>×</button>
            </div>
            <label className="clickhouse-add-field">
              <span>ClickHouse 表</span>
              <select value={selectedTable} onChange={(event) => setSelectedTable(event.target.value)}>
                <option value="">请选择一张表</option>
                {addableTables.map((table) => <option key={table.name} value={table.name}>{table.name}{table.time_column ? ` · ${table.time_column}` : " · 无时间字段"}</option>)}
              </select>
            </label>
            {addableTables.length === 0 && !addError && <p className="clickhouse-utc-note">当前没有尚未添加的 ClickHouse 表。</p>}
            {addError && <p className="monitor-logs-error">{addError}</p>}
            <div className="monitor-modal-actions">
              <button type="button" onClick={() => setAddOpen(false)}>取消</button>
              <button className="primary" type="button" disabled={adding || !selectedTable} onClick={() => void addMonitor()}>{adding ? "正在添加…" : "添加监控"}</button>
            </div>
          </section>
        </div>
      )}

      {settingsOpen && (
        <div className="monitor-modal-backdrop">
          <button className="monitor-modal-dismiss" type="button" aria-label="关闭数据监控设置" onClick={() => setSettingsOpen(false)} />
          <section className="monitor-modal clickhouse-settings-modal" role="dialog" aria-modal="true" aria-labelledby="clickhouse-settings-title">
            <div className="monitor-modal-head">
              <div><span>DATA FRESHNESS</span><h2 id="clickhouse-settings-title">数据监控设置</h2></div>
              <button type="button" aria-label="关闭" onClick={() => setSettingsOpen(false)}>×</button>
            </div>
            <div className="clickhouse-monitor-rules">
              <div><span>后台刷新</span><b>每 1 分钟</b></div>
              <div><span>健康标准</span><b>数据延迟不超过 60 秒</b></div>
              <div><span>时间基准</span><b>UTC / Unix 时间戳</b></div>
            </div>
            <p className="clickhouse-utc-note">监控项请通过模块右上角逐个添加，并在对应行右侧删除。页面只读取后台缓存，点击刷新图标可立即重新查询。</p>
            <div className="monitor-modal-actions">
              <button className="primary" type="button" onClick={() => setSettingsOpen(false)}>完成</button>
            </div>
          </section>
        </div>
      )}
    </>
  );
}

function RefreshIcon({ spinning = false }: { spinning?: boolean }) {
  return (
    <svg className={spinning ? "is-spinning" : ""} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M20 6v5h-5" />
      <path d="M18.2 16.1A8 8 0 1 1 19.7 9" />
    </svg>
  );
}

function formatUtc(timestamp: number | null) {
  if (!timestamp) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "UTC",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(timestamp * 1000).replaceAll("/", "-") + " UTC";
}

function formatLag(seconds: number | null) {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分`;
}
