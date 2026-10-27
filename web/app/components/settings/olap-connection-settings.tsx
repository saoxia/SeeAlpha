"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

type OlapSettings = {
  address: string;
  port: number;
  database: string;
  username: string;
  password_configured: boolean;
  updated_at: number | null;
};

async function olapRequest(path = "", init?: RequestInit) {
  const response = await fetch(`/api/system/olap${path}`, {
    ...init,
    credentials: "same-origin",
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.detail || "OLAP 连接操作失败。");
  return payload;
}

export function OlapConnectionSettings() {
  const [address, setAddress] = useState("");
  const [port, setPort] = useState("8123");
  const [database, setDatabase] = useState("default");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [saved, setSaved] = useState<OlapSettings | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const loadSettings = useCallback(async () => {
    try {
      const settings = await olapRequest() as OlapSettings;
      setSaved(settings);
      setAddress(settings.address);
      setPort(String(settings.port));
      setDatabase(settings.database);
      setUsername(settings.username);
      setError("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "无法读取 OLAP 配置。");
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadSettings(), 0);
    return () => window.clearTimeout(timer);
  }, [loadSettings]);

  async function saveSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    setError("");
    try {
      const settings = await olapRequest("", {
        method: "PUT",
        body: JSON.stringify({
          address,
          port: Number(port),
          database,
          username,
          password: password || null,
        }),
      }) as OlapSettings;
      setSaved(settings);
      setPassword("");
      setMessage("OLAP 连接已加密保存，Data Ops 可以直接使用。 ");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "保存失败。");
    } finally {
      setBusy(false);
    }
  }

  async function testConnection() {
    setBusy(true);
    setMessage("");
    setError("");
    try {
      const result = await olapRequest("/test", { method: "POST" });
      setMessage(`${result.message} 共发现 ${result.table_count} 张表。`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "连接测试失败。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="settings-card" onSubmit={saveSettings}>
      <div className="settings-card-head">
        <span>02</span>
        <div><h2>OLAP 数据库连接</h2><p>配置 ClickHouse 数据源，供 Data Ops 数据水位监控使用。</p></div>
        <div className="settings-head-actions">
          <i>DATABASE</i>
          <button type="button" className="secondary" disabled={busy || !saved?.password_configured} onClick={() => void testConnection()}>测试连接</button>
          <button type="submit" className="primary" disabled={busy}>{busy ? "正在处理…" : "保存配置"}</button>
        </div>
      </div>

      <div className={`github-connection-state ${saved?.password_configured ? "configured" : "missing"}`}>
        <span>{saved?.password_configured ? "已配置" : "未配置"}</span>
        <b>{saved?.password_configured ? `${saved.username}@${saved.address}:${saved.port}` : "请重新填写并保存连接"}</b>
        {saved?.updated_at && <small>上次保存：{new Date(saved.updated_at * 1000).toLocaleString("zh-CN")}</small>}
      </div>

      <div className="form-grid">
        <label className="field field-wide"><span>连接地址</span><input value={address} onChange={(event) => setAddress(event.target.value)} required placeholder="http://clickhouse.example.com" /></label>
        <label className="field"><span>端口</span><input value={port} onChange={(event) => setPort(event.target.value)} required type="number" min="1" max="65535" /></label>
        <label className="field"><span>数据库</span><input value={database} onChange={(event) => setDatabase(event.target.value)} required pattern="[A-Za-z_][A-Za-z0-9_]*" /></label>
        <label className="field"><span>用户名</span><input value={username} onChange={(event) => setUsername(event.target.value)} required autoComplete="username" /></label>
        <label className="field"><span>密码</span><input value={password} onChange={(event) => setPassword(event.target.value)} required={!saved?.password_configured} type="password" autoComplete="new-password" placeholder={saved?.password_configured ? "留空则保持现有密码" : "••••••••••••"} /></label>
      </div>
      {(message || error) && <div className={`settings-feedback ${error ? "error" : "success"}`}>{error || message}</div>}
    </form>
  );
}
