"use client";

import { FormEvent, useEffect, useState } from "react";

type WorkerTenant = { id: number; name: string; status: string };
type WorkerSettings = {
  max_workers: number;
  timeout_seconds: number;
  default_max_workers: number;
  default_timeout_seconds: number;
  source: "default" | "saved";
  running: number;
  queued: number;
};

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.detail || "Worker 配置请求失败。");
  return payload as T;
}

export function BacktestWorkerSettings() {
  const [tenants, setTenants] = useState<WorkerTenant[]>([]);
  const [tenantId, setTenantId] = useState("");
  const [config, setConfig] = useState<WorkerSettings | null>(null);
  const [workers, setWorkers] = useState("1");
  const [timeout, setTimeoutValue] = useState("3600");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    void api<{ items: WorkerTenant[] }>("/api/admin/backtest-workers/tenants", {
      signal: controller.signal,
    }).then(({ items }) => {
      setTenants(items);
      setTenantId(items.length ? String(items[0].id) : "");
      setError("");
    }).catch((caught) => {
      if (!controller.signal.aborted) {
        setError(caught instanceof Error ? caught.message : "无法加载开发租户。");
      }
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!tenantId) {
      setConfig(null);
      return;
    }
    const controller = new AbortController();
    setConfig(null);
    setNotice("");
    setError("");
    setLoading(true);
    void api<WorkerSettings>(`/api/tenants/${tenantId}/backtest/worker-settings`, {
      signal: controller.signal,
    }).then((value) => {
      if (controller.signal.aborted) return;
      setConfig(value);
      setWorkers(String(value.max_workers));
      setTimeoutValue(String(value.timeout_seconds));
    }).catch((caught) => {
      if (!controller.signal.aborted) {
        setError(caught instanceof Error ? caught.message : "无法读取 Worker 设置。");
      }
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [tenantId]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const maxWorkers = Number(workers);
    const timeoutSeconds = Number(timeout);
    if (!Number.isInteger(maxWorkers) || maxWorkers < 1 || maxWorkers > 8 ||
        !Number.isInteger(timeoutSeconds) || timeoutSeconds < 1 || timeoutSeconds > 86400) {
      setError("并发数量须为 1–8，执行超时须为 1–86400 秒。");
      return;
    }
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const saved = await api<WorkerSettings>(
        `/api/tenants/${tenantId}/backtest/worker-settings`,
        {
          method: "PUT",
          body: JSON.stringify({ max_workers: maxWorkers, timeout_seconds: timeoutSeconds }),
        },
      );
      setConfig(saved);
      setWorkers(String(saved.max_workers));
      setTimeoutValue(String(saved.timeout_seconds));
      setNotice("配置已保存并生效。正在运行的回测继续使用启动时的超时限制。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存配置失败。");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="settings-page" aria-label="回测 Worker 配置">
      <form className="settings-card" onSubmit={(event) => void save(event)}>
        <header className="settings-card-head">
          <span>ENGINE</span>
          <div>
            <h2>回测 Worker 管理</h2>
            <p>按开发租户配置容器内回测子进程的最大并发数和执行超时。</p>
          </div>
          <div className="settings-head-actions">
            <button className="primary" type="submit" disabled={!config || loading || saving}>
              {saving ? "正在保存…" : "保存配置"}
            </button>
          </div>
        </header>
        <div className="form-grid">
          <label className="field field-wide">
            <span>开发租户 / Engine</span>
            <select
              value={tenantId}
              onChange={(event) => setTenantId(event.target.value)}
              disabled={saving || loading || tenants.length === 0}
            >
              {tenants.length === 0 && <option value="">暂无可配置的开发租户</option>}
              {tenants.map((tenant) => (
                <option key={tenant.id} value={tenant.id}>{tenant.name}（#{tenant.id}）</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>最大并发 Worker 数（1–8）</span>
            <input
              type="number" required min={1} max={8} step={1}
              value={workers} disabled={!config || saving}
              onChange={(event) => setWorkers(event.target.value)}
            />
          </label>
          <label className="field">
            <span>单个回测执行超时（秒，1–86400）</span>
            <input
              type="number" required min={1} max={86400} step={1}
              value={timeout} disabled={!config || saving}
              onChange={(event) => setTimeoutValue(event.target.value)}
            />
          </label>
        </div>
        {config && (
          <p>
            配置来源：{config.source === "saved" ? "管理后台已保存" : "Engine 默认值"}；
            默认并发 {config.default_max_workers}、默认超时 {config.default_timeout_seconds} 秒；
            当前运行 {config.running} 个、等待 {config.queued} 个。
          </p>
        )}
        <p>降低并发不会取消正在运行的任务；超时修改仅影响新启动的 Worker。配置保存在对应 Engine 的 SQLite 中，容器重启后保留。</p>
        {loading && <p>正在读取配置…</p>}
        {(error || notice) && (
          <div className={`settings-feedback ${error ? "error" : "success"}`} role={error ? "alert" : "status"}>
            {error || notice}
          </div>
        )}
      </form>
    </section>
  );
}
