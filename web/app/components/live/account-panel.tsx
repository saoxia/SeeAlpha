import type { ExchangeAccount, ExchangePortfolio } from "../types";
import { fixedDecimal, PortfolioPanelState } from "./portfolio-panel-shared";

type AccountPanelProps = {
  account: ExchangeAccount | null;
  portfolio: ExchangePortfolio | null;
  loading: boolean;
  error: string;
  overview: boolean;
  scopeName: string | undefined;
};

export function AccountPanel({ account, portfolio, loading, error, overview, scopeName }: AccountPanelProps) {
  const assets = portfolio?.assets ?? [];
  const smallAssets = assets.filter((asset) => asset.small && !asset.stale && typeof asset.value_usdt === "string");
  const mainAssets = assets.filter((asset) => !smallAssets.includes(asset));
  const renderAssets = (items: typeof assets) => items.map((asset) => (
    <li key={`${asset.account_id}-${asset.currency}`}>
      <b>{asset.currency}{overview ? ` · ${asset.account_name}` : ""}</b>
      <span>{asset.balance}</span>
      <span>{asset.stale || typeof asset.value_usdt !== "string" ? "暂未估值" : `≈ ${fixedDecimal(asset.value_usdt, 4)} USDT`}</span>
      {asset.priced_at && <small>价格时间：{new Date(asset.priced_at * 1000).toLocaleString("zh-CN")}</small>}
      {asset.stale && typeof asset.value_usdt === "string" && <small>上次估值：{fixedDecimal(asset.value_usdt, 4)} USDT（旧价参考）</small>}
      {asset.error && <small className="error">{asset.error}</small>}
    </li>
  ));
  return (
    <>
      <h2>{scopeName ? `${scopeName} · 账户` : "账户"}</h2>
      <PortfolioPanelState account={account} loading={loading} error={error} overview={overview} />
      {!loading && !error && (overview || account) && portfolio && (portfolio.errors.account
        ? <p className="portfolio-state error">{portfolio.errors.account}</p>
        : <>
            {portfolio.warnings.account && <p className="portfolio-warning">{portfolio.warnings.account}</p>}
            <section className="account-summary" aria-label="账户概览">
              <section className="account-balance-block" aria-label="预估资产、钱包余额与未实现盈亏">
                <header className="account-section-head"><span>01</span><h3>钱包与盈亏</h3></header>
                <div className="account-balance-grid">
                  <div className="account-total-equity"><small>预估资产</small><b>{fixedDecimal(portfolio.account.total_equity, 2)}</b><span>{portfolio.account.currency}</span></div>
                  <div><small>钱包余额</small><b>{fixedDecimal(portfolio.account.wallet_balance, 2)}</b><span>{portfolio.account.currency}</span></div>
                  <div><small>未实现盈亏</small><b className={Number(portfolio.account.unrealized_pnl) < 0 ? "down" : "up"}>{fixedDecimal(portfolio.account.unrealized_pnl, 2)}</b><span>{portfolio.account.currency}</span></div>
                </div>
              </section>
              <section className="account-risk-block" aria-label="账户风险">
                <header className="account-section-head"><span>02</span><h3>风险与杠杆</h3></header>
                <div className="account-risk-hero">
                  <small>账户风险率</small>
                  <strong>{fixedDecimal(portfolio.account.margin_ratio, 2)}%</strong>
                  <span>保证金比率</span>
                  <div className="risk-track" aria-hidden="true"><i style={{ width: `${Math.min(Number(portfolio.account.margin_ratio) || 0, 100)}%` }} /></div>
                </div>
                <div className="account-metric-cards">
                  <div><small>账户维持保证金</small><b>{fixedDecimal(portfolio.account.maintenance_margin, 2)}</b><span>{portfolio.account.currency}</span></div>
                  <div><small>仓位估值</small><b>{fixedDecimal(portfolio.account.position_value, 2)}</b><span>{portfolio.account.currency}</span></div>
                  <div><small>实际杠杆</small><b>{fixedDecimal(portfolio.account.effective_leverage, 4)}</b><span>X</span></div>
                </div>
              </section>
            </section>
          </>)}
      {!loading && !error && assets.length > 0 && <section className="account-assets" aria-label="分币种资产">
        <h3>资产明细 · USDT 估值</h3>
        <ul>{renderAssets(mainAssets)}</ul>
        {smallAssets.length > 0 && <details>
          <summary>小额资产（低于 5 USDT）· {smallAssets.length} 项</summary>
          <p>小额资产仍计入估值，价格最多缓存 1 小时；点击“刷新资产”立即更新。</p>
          <ul>{renderAssets(smallAssets)}</ul>
        </details>}
      </section>}
    </>
  );
}
