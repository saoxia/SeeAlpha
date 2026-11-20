"use client";

import { FormEvent, useEffect, useState } from "react";
import {
  latestBasketBacktest, latestGenericPortfolioBacktest, latestIssuedSpotPerpBacktest,
  listIssuedStrategyLoads, runBasketBacktest, runGenericPortfolioBacktest,
  runIssuedSpotPerpBacktest,
  type BasketBacktestResult, type BasketMode, type GenericPortfolioLeg, type IssuedStrategyLoad,
} from "@/app/features/strategies/api";
import { basketReportMetrics } from "@/app/features/strategies/basket-report";

export function BasketBacktestPanel({
  tenantId, accountId,
}: { tenantId: number; accountId: number }) {
  const [mode, setMode] = useState<BasketMode | "issued_spot_perp" | "generic">("funding_carry");
  const [issuedLoads, setIssuedLoads] = useState<IssuedStrategyLoad[]>([]);
  const [issuanceId, setIssuanceId] = useState("");
  const [otherIssuances, setOtherIssuances] = useState("");
  const [marketLegs, setMarketLegs] = useState("spot:ETHUSDT\nfutures_um:BTCUSDT");
  const [interval, setInterval] = useState<"1m" | "5m">("1m");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [primaryBalance, setPrimaryBalance] = useState("500");
  const [marginBalance, setMarginBalance] = useState("500");
  const [grossLimit, setGrossLimit] = useState("1000");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BasketBacktestResult | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void listIssuedStrategyLoads(tenantId).then(loads => {
      if (!active) return;
      setIssuedLoads(loads.filter(load =>
        String(load.account_id) === String(accountId)
        && load.purpose === "development"
      ));
      setIssuanceId("");
    }).catch(() => {
      if (active) setIssuedLoads([]);
    });
    return () => { active = false; };
  }, [tenantId, accountId]);

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
      if (mode === "generic") {
        if (!issuanceId) throw new Error("请先选择一个已下发的开发策略实例。");
        const extra = otherIssuances.split(",").map(value => value.trim()).filter(Boolean);
        const ids = [issuanceId, ...extra];
        if (
          ids.length > 16 || new Set(ids).size !== ids.length
          || ids.some(id => !/^[0-9]+$/.test(id))
          || ids.some(id => !issuedLoads.some(load => String(load.id) === id))
        ) throw new Error("策略编号必须属于当前账户且不能重复（最多16个）。");
        const legs: GenericPortfolioLeg[] = marketLegs.split(/\r?\n/).map(line => {
          const [product, symbol, ...rest] = line.trim().split(":");
          if (
            rest.length || !["spot", "futures_um"].includes(product)
            || !/^[A-Z0-9-]{2,64}$/.test(symbol ?? "")
          ) throw new Error("每行必须为 spot:ETHUSDT 或 futures_um:BTCUSDT 等合法产品合约。");
          return { product: product as GenericPortfolioLeg["product"], symbol };
        });
        if (legs.length < 1 || legs.length > 8
          || new Set(legs.map(leg => leg.product + ":" + leg.symbol)).size !== legs.length
        ) throw new Error("请选择1至8条不重复的市场腿。");
        setResult(await runGenericPortfolioBacktest(tenantId, accountId, {
          issuance_ids: ids, legs, start: first.toISOString(), end: last.toISOString(),
          interval, starting_spot_balance: primaryBalance,
          starting_perpetual_balance: marginBalance, max_gross_notional: grossLimit,
        }));
      } else if (mode === "issued_spot_perp") {
        if (!issuanceId) throw new Error("请先选择当前账户已下发的策略实例。");
        setResult(await runIssuedSpotPerpBacktest(tenantId, accountId, {
          issuance_id: issuanceId, start: first.toISOString(), end: last.toISOString(),
          interval, starting_spot_balance: primaryBalance,
          starting_perpetual_balance: marginBalance, max_gross_notional: grossLimit,
        }));
      } else {
        setResult(await runBasketBacktest(tenantId, accountId, {
          mode, start: first.toISOString(), end: last.toISOString(),
          interval, starting_primary_balance: primaryBalance,
          starting_perpetual_balance: marginBalance, max_gross_notional: grossLimit,
        }));
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "组合回测失败。");
    } finally {
      setBusy(false);
    }
  }

  async function readLatest() {
    setBusy(true); setError("");
    try {
      setResult(mode === "generic"
        ? await latestGenericPortfolioBacktest(tenantId, accountId)
        : mode === "issued_spot_perp"
          ? await latestIssuedSpotPerpBacktest(tenantId, accountId)
          : await latestBasketBacktest(tenantId, accountId, mode));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "无法读取组合回测。");
    } finally {
      setBusy(false);
    }
  }

  const metrics = result ? basketReportMetrics(result) : null;

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
            <option value="issued_spot_perp">指定一个已下发策略：Spot + 永续</option>
            <option value="generic">通用多标的 · 原生模拟（开发）</option>
          </select>
        </label>
        {(mode === "issued_spot_perp" || mode === "generic") && (
          <label>已下发策略实例
            <select required value={issuanceId} onChange={event => setIssuanceId(event.target.value)}>
              <option value="">请选择此账户的开发策略</option>
              {issuedLoads.map(load => (
                <option key={load.id} value={String(load.id)}>
                  #{load.id} · {load.code_key} · {String(load.parameters.symbol ?? "")}
                </option>
              ))}
            </select>
          </label>
        )}
        {mode === "generic" && (
          <>
            <label>其他开发策略编号（选填，英文逗号分隔）
              <input value={otherIssuances} onChange={event => setOtherIssuances(event.target.value)}
                placeholder="例如 24,25（须为当前账户已下发策略）" />
            </label>
            <label>市场腿（每行 product:SYMBOL，最多8条）
              <textarea required rows={4} value={marketLegs}
                onChange={event => setMarketLegs(event.target.value)} />
            </label>
          </>
        )}
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
        <label>{mode === "protected_options" ? "期权现金（USDT）" : "现货现金（USDT）"}
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
      {mode === "generic" && (
        <p>使用一个 NautilusTrader 原生模拟引擎处理多标的。所选已下发开发策略需通过
          subscribe_market_bundle 声明全部市场腿，并通过 publish_basket 提交目标。
          只有策略在 publish_basket 显式声明 spot_perpetual_hedges，才会校验同标的现货多头与
          永续空头的等量目标及阶段成交；不同标的并非自动对冲。
          当前通用模式仅支持 USDT 现货和 USD-M 永续；资金费结算、期权通用回测及实盘下单均不在此入口。</p>
      )}
      {mode === "issued_spot_perp" && (
        <p>选中的策略必须通过 on_market_snapshot 发布完整且等额的现货多头与永续空头目标；
          缺少历史资金费证据或任意一腿未能完整模拟成交时会拒绝回测，不会启动真实订单。</p>
      )}
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
      {result && result.fills.length > 0 && (
        <details className="strategy-details">
          <summary>查看模拟成交审计（最后 50 笔，共 {result.fills.length} 笔）</summary>
          <table>
            <thead>
              <tr>
                <th scope="col">模拟合约</th><th scope="col">成交数量</th>
                <th scope="col">模拟成交价</th><th scope="col">手续费（原始记录）</th>
                <th scope="col">执行阶段</th>
              </tr>
            </thead>
            <tbody>
              {result.fills.slice(-50).reverse().map((fill, index) => (
                <tr key={fill.client_order_id
                  ? fill.client_order_id + "-" + index : fill.instrument_id + "-" + index}>
                  <td>{fill.instrument_id}</td>
                  <td>{fill.quantity}</td>
                  <td>{fill.price}</td>
                  <td>{fill.commission === undefined ? "未提供" : fill.commission}
                    {fill.currency ? " " + fill.currency : ""}</td>
                  <td>{fill.phase ?? "未提供阶段信息"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <small>以上均为历史开发模拟回报，不是交易所真实成交或生产审计记录。
            手续费保留原始金额和币种，不在缺少兑换证据时跨币种相加。</small>
        </details>
      )}
      {metrics && (metrics.positions.length > 0 || metrics.accounts.length > 0) && (
        <section className="strategy-details" aria-label="历史模拟组合快照">
          <h3>最后一根 K 线的模拟组合快照</h3>
          {metrics.positions.length > 0 && (
            <>
              <h4>各交易产品持仓（模拟结果，不是实时账户仓位）</h4>
              <table>
                <thead><tr><th scope="col">产品与含义</th><th scope="col">数量</th></tr></thead>
                <tbody>{metrics.positions.map(item => (
                  <tr key={item.label}><th scope="row">{item.label}</th><td>{item.value}</td></tr>
                ))}</tbody>
              </table>
            </>
          )}
          {metrics.legDrift !== null && <p>模拟现货／永续腿间数量偏差：{metrics.legDrift}</p>}
          {metrics.hedges.length > 0 && (
            <p>Engine 对冲阶段验证：{metrics.hedges.map(item =>
              item.symbol + " Spot 多头 / USD-M 空头（稳定阶段允许 BASE 数量偏差 "
              + item.baseQuantityDriftLimit + "）"
            ).join("；")}。这是模拟成交核验，不代表跨产品原子执行或资金费盈利。</p>
          )}
          {metrics.accounts.length > 0 && (
            <>
              <h4>分账户模拟权益</h4>
              <table>
                <thead><tr><th scope="col">账户</th><th scope="col">权益</th></tr></thead>
                <tbody>{metrics.accounts.map(item => (
                  <tr key={item.label}><th scope="row">{item.label}</th><td>{item.value}</td></tr>
                ))}</tbody>
              </table>
            </>
          )}
          {metrics.fundingObservations !== null && (
            <p>历史资金费观测：{metrics.fundingObservations} 条（不等于逐笔资金划转对账）。</p>
          )}
          {metrics.targetEvents !== null && (
            <p>有来源的模拟目标事件：{metrics.targetEvents} 条（不等于实际成交）。</p>
          )}
          {metrics.latestTargetEvent && (
            <>
              <h4>最新历史组合目标（并非真实账户委托）</h4>
              <p>事件时间（UTC，毫秒精度）：{new Date(metrics.latestTargetEvent.tsMs).toISOString()}</p>
              {metrics.latestTargetEvent.memberCount !== null && (
                <p>参与策略成员数量：{metrics.latestTargetEvent.memberCount}</p>
              )}
              {metrics.latestTargetEvent.action && (
                <p>策略模型动作：{metrics.latestTargetEvent.action}</p>
              )}
              <table>
                <thead>
                  <tr><th scope="col">产品 / 合约</th><th scope="col">目标数量</th></tr>
                </thead>
                <tbody>{metrics.latestTargetEvent.targets.map(item => (
                  <tr key={item.label}>
                    <th scope="row">{item.label}</th><td>{item.quantity}</td>
                  </tr>
                ))}</tbody>
              </table>
              <small>目标不是已成交仓位；完整阶段状态、真实账户盈亏归因和生产执行审批尚未提供。</small>
            </>
          )}
          <small>全部数字来自 Engine 已保存的开发回测报告；无实时持仓查询、无自动下单或独立收益归因。</small>
        </section>
      )}
    </section>
  );
}
