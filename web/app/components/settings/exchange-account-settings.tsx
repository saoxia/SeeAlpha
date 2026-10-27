"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { exchangeRequest } from "./exchange-account-api";
import type { BalanceResponse, ExchangeAccount, ExchangeEnvironment, ExchangeId, MarketType } from "./exchange-account-types";
import { ExchangeMarketSelector } from "./exchange-market-selector";
import { ExchangeIcon } from "../brand/exchange-icon";
import { useExchangeMarketCatalog } from "@/app/features/settings/use-exchange-market-catalog";

function exchangeName(exchange: ExchangeId) {
  return exchange === "binance" ? "Binance" : "OKX";
}

function environmentName(environment: ExchangeEnvironment) {
  return environment === "production" ? "正式环境" : "测试环境";
}

export function ExchangeAccountSettings() {
  const [accounts, setAccounts] = useState<ExchangeAccount[]>([]);
  const [balances, setBalances] = useState<BalanceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | "form" | "balances" | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<ExchangeAccount | null>(null);
  const [exchange, setExchange] = useState<ExchangeId>("binance");
  const [accountName, setAccountName] = useState("");
  const [environment, setEnvironment] = useState<ExchangeEnvironment>("production");
  const [apiKey, setApiKey] = useState("");
  const [secret, setSecret] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [marketType, setMarketType] = useState<MarketType>("linear");
  const [marketSearch, setMarketSearch] = useState("");
  const [marketSymbols, setMarketSymbols] = useState<string[]>([]);
  const {
    markets,
    loading: marketLoading,
    error: marketError,
  } = useExchangeMarketCatalog({
    enabled: formOpen,
    exchange,
    environment,
    marketType,
    search: marketSearch,
  });

  const loadAccounts = useCallback(async () => {
    try {
      setAccounts(await exchangeRequest<ExchangeAccount[]>());
      setError("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "无法读取交易所账户。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadAccounts(), 0);
    return () => window.clearTimeout(timer);
  }, [loadAccounts]);

  function resetSecrets() {
    setApiKey("");
    setSecret("");
    setPassphrase("");
  }

  function openCreate() {
    setEditing(null);
    setExchange("binance");
    setAccountName("");
    setEnvironment("production");
    setMarketType("linear");
    setMarketSearch("");
    setMarketSymbols([]);
    resetSecrets();
    setMessage("");
    setError("");
    setFormOpen(true);
  }

  function openEdit(account: ExchangeAccount) {
    setEditing(account);
    setExchange(account.exchange);
    setAccountName(account.account_name);
    setEnvironment(account.environment);
    setMarketType("linear");
    setMarketSearch("");
    setMarketSymbols(account.market_symbols);
    resetSecrets();
    setMessage("");
    setError("");
    setFormOpen(true);
  }

  async function saveAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusyId("form");
    setMessage("");
    setError("");
    if (marketSymbols.length === 0) {
      setError("请至少选择一个交易对。");
      setBusyId(null);
      return;
    }
    try {
      if (editing) {
        await exchangeRequest<ExchangeAccount>(`/${editing.id}`, {
          method: "PUT",
          body: JSON.stringify({
            account_name: accountName,
            environment,
            api_key: apiKey || null,
            secret: secret || null,
            passphrase: passphrase || null,
            market_symbols: marketSymbols,
          }),
        });
        setMessage(`${exchangeName(editing.exchange)} 账户已更新。`);
      } else {
        await exchangeRequest<ExchangeAccount>("", {
          method: "POST",
          body: JSON.stringify({
            exchange,
            account_name: accountName,
            environment,
            api_key: apiKey,
            secret,
            passphrase: passphrase || null,
            market_symbols: marketSymbols,
          }),
        });
        setMessage(`${exchangeName(exchange)} 账户已加密保存。`);
      }
      resetSecrets();
      setFormOpen(false);
      setBalances(null);
      await loadAccounts();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "保存交易所账户失败。");
    } finally {
      setBusyId(null);
    }
  }

  async function testAccount(account: ExchangeAccount) {
    setBusyId(account.id);
    setMessage("");
    setError("");
    try {
      const result = await exchangeRequest<{ message: string }>(`/${account.id}/test`, { method: "POST" });
      setMessage(result.message);
      setBalances(null);
      await loadAccounts();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "连接测试失败。");
      await loadAccounts();
    } finally {
      setBusyId(null);
    }
  }

  async function removeAccount(account: ExchangeAccount) {
    if (!window.confirm(`确定删除“${account.account_name}”吗？已保存的凭据将一并删除。`)) return;
    setBusyId(account.id);
    setMessage("");
    setError("");
    try {
      await exchangeRequest<void>(`/${account.id}`, { method: "DELETE" });
      setMessage(`${exchangeName(account.exchange)} 账户已删除。`);
      setBalances(null);
      await loadAccounts();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "删除交易所账户失败。");
    } finally {
      setBusyId(null);
    }
  }

  async function refreshBalances() {
    setBusyId("balances");
    setMessage("");
    setError("");
    try {
      setBalances(await exchangeRequest<BalanceResponse>("/balances"));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "无法聚合钱包余额。");
    } finally {
      setBusyId(null);
    }
  }

  const balanceByAccount = new Map(balances?.items.map((item) => [item.account.id, item]));

  function toggleMarket(symbol: string) {
    setMarketSymbols((current) => current.includes(symbol)
      ? current.filter((item) => item !== symbol)
      : current.length >= 30 ? current : [...current, symbol]);
  }

  return (
    <section className="settings-card exchange-settings-card">
      <div className="settings-card-head">
        <span>01</span>
        <div><h2>交易所账户</h2><p>通过原生适配器查询 Binance USD-M 永续账户余额与仓位。</p></div>
        <i>EXCHANGES</i>
      </div>

      <div className="exchange-toolbar">
        <div>
          <b>{accounts.length} 个账户</b>
          <small>Key 仅在服务端解密使用，页面不会读取原文。</small>
        </div>
        <div>
          <button type="button" disabled={busyId !== null || accounts.length === 0} onClick={() => void refreshBalances()}>
            {busyId === "balances" ? "正在聚合…" : "聚合余额"}
          </button>
          <button type="button" className="primary" disabled={busyId !== null} onClick={openCreate}>新增账户</button>
        </div>
      </div>

      {loading && <p className="exchange-empty">正在读取交易所账户…</p>}
      {!loading && accounts.length === 0 && <p className="exchange-empty">尚未配置账户。建议使用无提现权限、绑定服务器出口 IP 的专用 API Key。</p>}

      <div className="exchange-account-list">
        {accounts.map((account) => {
          const balance = balanceByAccount.get(account.id);
          const assets = balance ? Object.entries(balance.balances) : [];
          return (
            <article className="exchange-account-row" key={account.id}>
              <span className={`exchange-logo ${account.exchange}`}><ExchangeIcon exchange={account.exchange} /></span>
              <div className="exchange-account-identity">
                <small>{exchangeName(account.exchange)} · {environmentName(account.environment)}</small>
                <b>{account.account_name}</b>
                <span>Key {account.key_hint}</span>
                <span>{account.market_symbols.length} 个交易对</span>
              </div>
              <div className="exchange-account-status">
                <small>连接状态</small>
                <b className={account.last_test_status || "untested"}>
                  {account.last_test_status === "success" ? "已验证" : account.last_test_status === "failed" ? "验证失败" : "未测试"}
                </b>
                <span>{account.last_tested_at ? new Date(account.last_tested_at * 1000).toLocaleString("zh-CN") : "—"}</span>
              </div>
              <div className="exchange-account-balance">
                <small>钱包余额</small>
                {!balance && <span>点击“聚合余额”读取</span>}
                {balance?.error && <span className="error">{balance.error}</span>}
                {balance && !balance.error && assets.length === 0 && <span>当前没有非零余额</span>}
                {assets.slice(0, 6).map(([currency, amount]) => <b key={currency}>{currency} {amount}</b>)}
                {assets.length > 6 && <span>另有 {assets.length - 6} 种资产</span>}
              </div>
              <div className="exchange-account-actions">
                <button type="button" disabled={busyId !== null} onClick={() => void testAccount(account)}>{busyId === account.id ? "处理中…" : "测试"}</button>
                <button type="button" disabled={busyId !== null} onClick={() => openEdit(account)}>编辑</button>
                <button type="button" className="remove" disabled={busyId !== null} onClick={() => void removeAccount(account)}>删除</button>
              </div>
            </article>
          );
        })}
      </div>

      {balances && <p className="exchange-balance-time">余额聚合时间：{new Date(balances.checked_at * 1000).toLocaleString("zh-CN")}（10 秒内重复读取使用缓存）</p>}
      {(message || error) && <div className={`settings-feedback ${error ? "error" : "success"}`} aria-live="polite">{error || message}</div>}
      <p className="github-key-note">安全建议：仅授予读取和必要的交易权限，禁用提现权限，并在交易所后台绑定服务器出口 IP。</p>

      {formOpen && (
        <div className="monitor-modal-backdrop">
          <button className="monitor-modal-dismiss" type="button" aria-label="关闭账户编辑" onClick={() => setFormOpen(false)} />
          <form className="monitor-modal exchange-account-modal" onSubmit={saveAccount}>
            <div className="monitor-modal-head">
              <div><span>EXCHANGE ACCOUNT</span><h2>{editing ? "编辑账户" : "新增账户"}</h2></div>
              <button type="button" aria-label="关闭" onClick={() => setFormOpen(false)}>×</button>
            </div>
            <div className="form-grid">
              <label className="field"><span>交易所</span><select value={exchange} disabled={Boolean(editing)} onChange={(event) => { setExchange(event.target.value as ExchangeId); setMarketSymbols([]); }}><option value="binance">Binance</option><option value="okx" disabled>OKX（待接入）</option></select></label>
              <label className="field"><span>环境</span><select value={environment} onChange={(event) => { setEnvironment(event.target.value as ExchangeEnvironment); setMarketSymbols([]); }}><option value="production">正式环境</option><option value="sandbox">测试环境</option></select></label>
              <label className="field field-wide"><span>账户名称</span><input value={accountName} onChange={(event) => setAccountName(event.target.value)} maxLength={100} required placeholder="例如：主账户 / 策略账户 A" /></label>
              <label className="field field-wide"><span>API Key</span><input type="password" autoComplete="new-password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} required={!editing} placeholder={editing ? "留空则保持现有整组凭据" : "API Key"} /></label>
              <label className="field field-wide"><span>Secret Key</span><input type="password" autoComplete="new-password" value={secret} onChange={(event) => setSecret(event.target.value)} required={!editing} placeholder={editing ? "更换凭据时必须整组填写" : "Secret Key"} /></label>
              {exchange === "okx" && <label className="field field-wide"><span>Passphrase</span><input type="password" autoComplete="new-password" value={passphrase} onChange={(event) => setPassphrase(event.target.value)} required={!editing} placeholder={editing ? "更换凭据时必须整组填写" : "OKX Passphrase"} /></label>}
              <ExchangeMarketSelector
                markets={markets}
                marketType={marketType}
                search={marketSearch}
                selectedSymbols={marketSymbols}
                loading={marketLoading}
                error={marketError}
                onMarketTypeChange={setMarketType}
                onSearchChange={setMarketSearch}
                onToggleSymbol={toggleMarket}
              />
            </div>
            {editing && <p className="github-key-note">不填写凭据时只修改账户名称；切换环境或更换 Key 时，请完整填写全部凭据。</p>}
            <div className="monitor-modal-actions">
              <button type="button" disabled={busyId === "form"} onClick={() => setFormOpen(false)}>取消</button>
              <button type="submit" className="primary" disabled={busyId === "form"}>{busyId === "form" ? "正在保存…" : "加密保存"}</button>
            </div>
          </form>
        </div>
      )}
    </section>
  );
}
