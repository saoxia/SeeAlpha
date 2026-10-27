import type { ExchangeAccount, ExchangePortfolio } from "../types";
import { decimal, PortfolioPanelState } from "./portfolio-panel-shared";

type PositionsPanelProps = {
  account: ExchangeAccount | null;
  portfolio: ExchangePortfolio | null;
  loading: boolean;
  error: string;
  overview: boolean;
  scopeName: string | undefined;
};

export function PositionsPanel({ account, portfolio, loading, error, overview, scopeName }: PositionsPanelProps) {
  return (
    <>
      <h2>{scopeName ? `${scopeName} · 当前仓位` : "当前仓位"}</h2>
      <PortfolioPanelState account={account} loading={loading} error={error} overview={overview} />
      {!loading && !error && (overview || account) && portfolio && (portfolio.errors.positions
        ? <p className="portfolio-state error">{portfolio.errors.positions}</p>
        : <>
            {portfolio.warnings.positions && <p className="portfolio-warning">{portfolio.warnings.positions}</p>}
            {portfolio.positions.length
              ? portfolio.positions.map((position, index) => {
                  const pnl = Number(position.unrealized_pnl);
                  return (
                    <article className="portfolio-position position-compact" key={`${position.id}-${position.symbol}-${index}`}>
                      <header className="position-compact-head">
                        <div>
                          <b title={position.symbol}>{position.symbol}</b>
                          <span>{position.side.toUpperCase() || "--"}{position.leverage ? ` · ${decimal(position.leverage, 2)}×` : ""}</span>
                        </div>
                        <div className="position-pnl">
                          <small>未实现盈亏</small>
                          <strong className={Number.isFinite(pnl) && pnl < 0 ? "down" : "up"}>{decimal(position.unrealized_pnl)}</strong>
                        </div>
                      </header>
                      <div className="position-compact-meta">
                        {overview && <small className="portfolio-source">{position.account_name} · {position.exchange.toUpperCase()}</small>}
                        <small>{(position.margin_mode || "--").toUpperCase()} MARGIN</small>
                      </div>
                      <dl className="position-metrics">
                        <div><dt>数量</dt><dd>{decimal(position.contracts)}</dd></div>
                        <div><dt>仓位估值</dt><dd>{decimal(position.notional)}</dd></div>
                        <div><dt>开仓价格</dt><dd>{decimal(position.entry_price)}</dd></div>
                        <div><dt>强平价格</dt><dd>{decimal(position.liquidation_price)}</dd></div>
                      </dl>
                    </article>
                  );
                })
              : <p className="portfolio-state">当前没有未平仓仓位。</p>}
          </>)}
    </>
  );
}
