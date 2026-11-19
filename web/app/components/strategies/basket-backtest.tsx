"use client";

import { FormEvent, useState } from "react";
import {
  latestBasketBacktest, runBasketBacktest,
  type BasketBacktestResult, type BasketMode,
} from "@/app/features/strategies/api";

export function BasketBacktestPanel({
  tenantId, accountId,
}: { tenantId: number; accountId: number }) {
  const [mode, setMode] = useState<BasketMode>("funding_carry");
  const [interval, setInterval] = useState<"1m" | "5m">("1m");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [primaryBalance, setPrimaryBalance] = useState("500");
  const [marginBalance, setMarginBalance] = useState("500");
  const [grossLimit, setGrossLimit] = useState("1000");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BasketBacktestResult | null>(null);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const first = new Date(start);
    const last = new Date(end);
    if (
      !Number.isFinite(first.getTime()) || !Number.isFinite(last.getTime())
      || last.getTime() <= first.getTime()
    ) {
      setError("请选择有效且结束晚于开始的历史时间窗口。");
      return;
    }
    setBusy(true); setError(""); setResult(null);
    try {
      setResult(await runBasketBacktest(tenantId, accountId, {
        mode, start: first.toISOString(), end: last.toISOString(),
        interval, starting_primary_balance: primaryBalance,
        starting_perpetual_balance: marginBalance,
        max_gross_notional: grossLimit,
      }));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "组合回测失败。");
    } finally {
      setBusy(false);
    }
  }

  async function readLatest() {
    setBusy(true); setError("");
    try {
      setResult(await latestBasketBacktest(tenantId, accountId, mode));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "无法读取组合回测。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="strategy-details" aria-label="跨产品组合开发回测">
      <h2>跨产品组合开发回测</h2>
      <p>仅使用已授权 Demo 和历史行情运行模拟交易，不会提交实盘订单。
        两个模拟账户的初始资金之和不得超过所选虚拟账户额度。</p>
      <form className="strategy-load-fields" onSubmit={event => void submit(event)}>
        <label>研究组合
          <select value={mode} onChange={event => { setMode(event.target.value as BasketMode); setResult(null); }}>
            <option value="funding_carry">现货多头 + 永续空头（资金费）</option>
            <option value="protected_options">永续 + PUT/CALL 保护（到期前）</option>
          </select>
        </label>
        <label>历史开始（本地时间）
          <input required type="datetime-local" value={start} onChange={event => setStart(event.target.value)} />
        </label>
        <label>历史结束（本地时间）
          <input required type="datetime-local" value={end} onChange={event => setEnd(event.target.value)} />
        </label>
        <label>K 线周期
          <select value={interval} onChange={event => setInterval(event.target.value as "1m" | "5m")}>
            <option value="1m">1 分钟</option>
            <option value="5m">5 分钟</option>
          </select>
        </label>
        <label>{mode === "funding_carry" ? "现货现金（USDT）" : "期权现金（USDT）"}
          <input required type="number" min="0.00000001" step="any" value={primaryBalance}
            onChange={event => setPrimaryBalance(event.target.value)} />
        </label>
        <label>永续保证金（USDT）
          <input required type="number" min="0.00000001" step="any" value={marginBalance}
            onChange={event => setMarginBalance(event.target.value)} />
        </label>
        <label>最大总名义金额（USDT）
          <input required type="number" min="0.00000001" step="any" value={grossLimit}
            onChange={event => setGrossLimit(event.target.value)} />
        </label>
        <button type="submit" disabled={busy}>{busy ? "模拟中…" : "运行开发回测"}</button>
        <button type="button" disabled={busy} onClick={() => void readLatest()}>查看最新结果</button>
      </form>
      {mode === "protected_options" && (
        <p>期权策略仅在真实历史买卖报价和足额保护腿均可验证时运行；
          到期行权结算和真实交易所跨产品原子成交不在本次回测范围。</p>
      )}
      {error && <p role="alert" className="settings-feedback error">{error}</p>}
      {result && (
        <div className="settings-feedback" role="status">
          <p>开发模拟完成：{result.bar_count} 根 K 线，{result.fills.length} 笔模拟成交。</p>
          <p>初始资金 {result.starting_balance} USDT；
            最后一期模拟权益 {result.ending_balance} USDT。</p>
          <small>结果仅用于研究；不代表实际交易收益、未来盈利或跨腿原子执行能力。</small>
        </div>
      )}
    </section>
  );
}
