"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { BasketBacktestPanel } from "./basket-backtest";
import { fetchTenants } from "@/app/features/identity/api";
import type { TenantRecord } from "@/app/features/identity/model";
import {
  cloneStrategyPackage, issueStrategy, listStrategyPackages, listStrategyShares,
  listTenantAccounts, setStrategyVisibility, shareStrategyPackage,
  strategyArchiveUrl, unshareStrategyPackage, uploadStrategyPackage,
  type ShareRecipient, type StrategyPackage, type TenantAccount,
} from "@/app/features/strategies/api";

type View = "all" | "system" | "mine" | "shared" | "public";
const filters: Array<{ id: View; label: string }> = [
  { id: "all", label: "全部可用" },
  { id: "system", label: "系统 Demo" },
  { id: "mine", label: "我的策略" },
  { id: "shared", label: "分享给我" },
  { id: "public", label: "公开策略" },
];
const support: Record<string, string> = {
  demo_ema_crossover: "单品种回测示例",
  demo_funding_carry: "开发租户支持独立 CASH/MARGIN 资金费组合回测；实盘未开放",
  demo_multi_signal_portfolio: "单品种多信号回测示例",
  demo_option_hedged_bollinger: "支持保护腿先成交的到期前开发回测；实盘未开放",
};

export function StrategyLibrary({ userId, admin = false }: { userId: number; admin?: boolean }) {
  const [items, setItems] = useState<StrategyPackage[]>([]);
  const [filter, setFilter] = useState<View>("all");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [updateFiles, setUpdateFiles] = useState<Record<number, File>>({});
  const [recipients, setRecipients] = useState<Record<number, ShareRecipient[]>>({});
  const [username, setUsername] = useState<Record<number, string>>({});
  const [tenants, setTenants] = useState<TenantRecord[]>([]);
  const [tenantId, setTenantId] = useState("");
  const [accounts, setAccounts] = useState<TenantAccount[]>([]);
  const [accountId, setAccountId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [putSymbol, setPutSymbol] = useState("");
  const [callSymbol, setCallSymbol] = useState("");

  const refresh = useCallback(async () => {
    setItems(await listStrategyPackages());
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([listStrategyPackages(), admin ? Promise.resolve([]) : fetchTenants()])
      .then(([packages, rows]) => {
        if (!active) return;
        setItems(packages);
        setTenants(rows);
      }).catch((caught: unknown) => {
        if (active) setError(caught instanceof Error ? caught.message : "无法加载策略目录。");
      }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [admin]);

  useEffect(() => {
    if (!tenantId) return;
    let active = true;
    void listTenantAccounts(Number(tenantId)).then(rows => {
      if (!active) return;
      setAccounts(rows);
      setAccountId("");
    }).catch((caught: unknown) => {
      if (active) setError(caught instanceof Error ? caught.message : "无法读取租户账户。");
    });
    return () => { active = false; };
  }, [tenantId]);

  async function run(action: () => Promise<void>, message: string) {
    setBusy(true); setError(""); setNotice("");
    try {
      await action();
      await refresh();
      setNotice(message);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "策略操作失败。");
    } finally {
      setBusy(false);
    }
  }

  const visible = items.filter(item => filter === "all"
    || (filter === "system" && item.visibility === "system")
    || (filter === "mine" && item.owner_id === userId)
    || (filter === "shared" && item.owner_id !== userId && item.visibility === "private")
    || (filter === "public" && item.visibility === "public"));
  const activeTenant = tenants.find(row => String(row.id) === tenantId);
  const selectedPackage = items.find(row => row.versions.some(v => String(v.id) === versionId));
  const selectedVersion = selectedPackage?.versions.find(v => String(v.id) === versionId);

  function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    void run(async () => { await uploadStrategyPackage(file); setFile(null); }, "策略已创建。新用户策略默认私有。");
  }

  return (
    <section className="settings-page strategy-library" aria-label="策略库">
      <div className="settings-card">
        <h1>策略库</h1>
        <p>系统示例向所有用户开放；个人策略默认私有，可选择公开或指定用户分享。复制操作只创建个人策略包，不会启动交易。</p>
        {error && <p className="settings-feedback error" role="alert">{error}</p>}
        {notice && <p className="settings-feedback" role="status">{notice}</p>}
        <div className="strategy-tabs" role="group" aria-label="策略类型">
          {(admin ? filters.filter(x => x.id === "all" || x.id === "system") : filters).map(tab => (
            <button type="button" key={tab.id} aria-pressed={filter === tab.id}
              className={filter === tab.id ? "active" : ""} onClick={() => setFilter(tab.id)}>
              {tab.label}
            </button>
          ))}
          <button type="button" onClick={() => void run(async () => {}, "策略列表已刷新。")} disabled={busy}>刷新</button>
        </div>
        {loading ? <p>正在读取策略目录…</p> : visible.length === 0 ? <p>暂无符合条件的策略。</p> : (
          <div className="strategy-grid">
            {visible.map(item => {
              const owned = item.owner_id === userId && !admin;
              const system = item.visibility === "system";
              const latest = item.versions[item.versions.length - 1];
              return (
                <article className="strategy-item" key={item.id}>
                  <div className="strategy-item-head">
                    <div><h2>{item.name}</h2><code>{item.code_key}</code></div>
                    <span>{system ? "系统 Demo" : owned ? item.visibility === "public" ? "我的 · 公开" : "我的 · 私有"
                      : item.visibility === "public" ? "公开策略" : "定向共享"}</span>
                  </div>
                  <p>{item.description ?? "暂无策略说明。"}</p>
                  {support[item.code_key] && <small className="strategy-support">{support[item.code_key]}</small>}
                  <div className="strategy-versions">
                    {item.versions.map(v => <span key={v.id}>v{v.version} · {v.grants.includes("development") ? "可申请开发加载" : "仅展示"}
                      {v.grants.includes("production") ? " · 正式授权" : ""}</span>)}
                  </div>
                  <div className="strategy-actions">
                    {!admin && <button disabled={busy || !latest} type="button"
                      onClick={() => void run(async () => {
                        await cloneStrategyPackage(item.id);
                        setFilter("mine");
                      }, "已复制到我的策略。")}>复制到我的策略</button>}
                    {(system || owned) && latest && <a href={strategyArchiveUrl(item.id, latest.id)} download>下载 ZIP</a>}
                    {owned && <button disabled={busy} type="button"
                      onClick={() => void run(async () => {
                        await setStrategyVisibility(item.id, item.visibility === "public" ? "private" : "public");
                      }, item.visibility === "public" ? "已改为私有策略。" : "策略现已公开。")}>
                      {item.visibility === "public" ? "设为私有" : "公开策略"}
                    </button>}
                  </div>
                  {owned && <details className="strategy-details">
                    <summary>版本更新与共享</summary>
                    <form onSubmit={event => {
                      event.preventDefault();
                      const nextFile = updateFiles[item.id];
                      if (!nextFile) return;
                      void run(async () => {
                        await uploadStrategyPackage(nextFile, item.id);
                        setUpdateFiles(old => {
                          const next = { ...old }; delete next[item.id]; return next;
                        });
                      }, "已新增策略版本，并给予开发租户授权。");
                    }}>
                      <label>上传同一策略标识的新版本 ZIP
                        <input type="file" accept=".zip" required
                          onChange={event => {
                            const value = event.currentTarget.files?.[0];
                            if (value) setUpdateFiles(old => ({ ...old, [item.id]: value }));
                          }} />
                      </label>
                      <button type="submit" disabled={busy || !updateFiles[item.id]}>新增版本</button>
                    </form>
                    <form onSubmit={event => {
                      event.preventDefault();
                      const target = username[item.id]?.trim();
                      if (!target) return;
                      void run(async () => {
                        const rows = await shareStrategyPackage(item.id, target);
                        setRecipients(old => ({ ...old, [item.id]: rows }));
                        setUsername(old => ({ ...old, [item.id]: "" }));
                      }, "已分享给指定用户。");
                    }}>
                      <label>分享给用户名
                        <input value={username[item.id] || ""} maxLength={64}
                          onChange={event => setUsername(old => ({ ...old, [item.id]: event.target.value }))}
                          placeholder="用户名" required />
                      </label>
                      <button type="submit" disabled={busy}>分享</button>
                    </form>
                    <button type="button" disabled={busy} onClick={() => void run(async () => {
                      const rows = await listStrategyShares(item.id);
                      setRecipients(old => ({ ...old, [item.id]: rows }));
                    }, "已读取共享名单。")}>查看共享名单</button>
                    {recipients[item.id]?.map(recipient => (
                      <div className="strategy-recipient" key={recipient.id}>
                        <span>{recipient.username}</span>
                        <button type="button" disabled={busy} onClick={() => void run(async () => {
                          const rows = await unshareStrategyPackage(item.id, recipient.id);
                          setRecipients(old => ({ ...old, [item.id]: rows }));
                        }, "已取消分享。")}>取消</button>
                      </div>
                    ))}
                  </details>}
                </article>
              );
            })}
          </div>
        )}
        <form className="strategy-upload" onSubmit={upload}>
          <h2>{admin ? "管理员登记系统策略" : "上传我的私有策略"}</h2>
          <p>ZIP 根目录需包含 seealpha-package.json 及策略入口源文件。管理员登记的包作为系统策略，普通用户上传的包默认私有。</p>
          <input type="file" accept=".zip" aria-label="上传策略 ZIP" required
            onChange={event => setFile(event.currentTarget.files?.[0] ?? null)} />
          <button type="submit" disabled={busy || !file}>上传策略</button>
        </form>
        {!admin && <form className="strategy-load" onSubmit={event => {
          event.preventDefault();
          if (!tenantId || !versionId || !accountId) return;
          void run(async () => {
            await issueStrategy(
              Number(tenantId), Number(versionId), Number(accountId),
              selectedPackage?.code_key === "demo_option_hedged_bollinger"
                ? { put_symbol: putSymbol.trim().toUpperCase(),
                    call_symbol: callSymbol.trim().toUpperCase() }
                : undefined,
            );
          }, "策略已下发至所选租户账户。");
        }}>
          <h2>加载策略到租户账户</h2>
          <p>仅在已有授权、账户和 Engine 支持的情况下才能加载。正式账户必须具有单独的正式授权，加载不等于自动开始交易。</p>
          <div className="strategy-load-fields">
            <label>策略版本
              <select value={versionId} onChange={event => setVersionId(event.target.value)} required>
                <option value="">选择策略版本</option>
                {items.flatMap(item => item.versions.map(v => (
                  <option key={v.id} value={v.id}>{item.name} · v{v.version}</option>
                )))}
              </select>
            </label>
            <label>租户
              <select value={tenantId} onChange={event => { setTenantId(event.target.value); setAccounts([]); setAccountId(""); }} required>
                <option value="">选择租户</option>
                {tenants.filter(t => t.status === "active").map(t => (
                  <option key={t.id} value={t.id}>{t.name} · {t.purpose === "production" ? "正式" : "开发"}</option>
                ))}
              </select>
            </label>
            <label>账户
              <select value={accountId} onChange={event => setAccountId(event.target.value)} required>
                <option value="">选择账户</option>
                {accounts.map(a => <option key={a.id} value={a.id}>{a.account_name}</option>)}
              </select>
            </label>
          </div>
          {selectedPackage?.code_key === "demo_option_hedged_bollinger" && (
            <div className="strategy-load-fields">
              <label>看跌期权合约代码
                <input required value={putSymbol} maxLength={64}
                  placeholder="ETH-YYMMDD-行权价-P" onChange={e => setPutSymbol(e.target.value)} />
              </label>
              <label>看涨期权合约代码
                <input required value={callSymbol} maxLength={64}
                  placeholder="ETH-YYMMDD-行权价-C" onChange={e => setCallSymbol(e.target.value)} />
              </label>
              <p>两条期权必须同标的、同到期，且历史报价真实可用。此配置仅允许开发回测。</p>
            </div>
          )}
          {activeTenant && selectedVersion && !selectedVersion.grants.includes(activeTenant.purpose) &&
            <p role="status">该版本尚未获得{activeTenant.purpose === "production" ? "正式" : "开发"}授权，无法加载。</p>}
          <button type="submit" disabled={busy || !tenantId || !accountId || !selectedVersion ||
            !activeTenant || !selectedVersion.grants.includes(activeTenant.purpose) ||
            (selectedPackage?.code_key === "demo_option_hedged_bollinger" &&
             (!putSymbol.trim() || !callSymbol.trim()))}>加载策略</button>
        </form>}
        {!admin && activeTenant?.purpose === "development" && accountId && (
          <BasketBacktestPanel tenantId={Number(tenantId)} accountId={Number(accountId)} />
        )}
      </div>
    </section>
  );
}
