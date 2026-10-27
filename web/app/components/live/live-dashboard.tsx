"use client";

import { useRef, useState } from "react";
import { PanelContent, panelNames, type PanelName } from "./panel-content";
import { EquityCandlestickChart } from "./equity-candlestick-chart";
import { ExchangeIcon } from "../brand/exchange-icon";
import { useLivePortfolio } from "@/app/features/live/use-live-portfolio";
import { formatAccountValue } from "@/app/features/live/portfolio-values";

export function LiveDashboard() {
  const [panel, setPanel] = useState<PanelName>("账户");
  const panelContentRef = useRef<HTMLDivElement>(null);
  const {
    accounts,
    venue,
    portfolio,
    loading: portfolioLoading,
    error: portfolioError,
    overviewEquity,
    accountEquities,
    selectVenue,
  } = useLivePortfolio();

  const selectedAccount = venue === "overview"
    ? null
    : accounts.find((account) => account.id === venue) ?? null;

  return (
    <>
      <section className="ticker" aria-label="交易所导航">
        <div className="venue-tabs">
          <button
            type="button"
            onClick={() => {
              if (venue === "overview") return;
              selectVenue("overview");
            }}
            className={`venue-tab${venue === "overview" ? " on" : ""}`}
            aria-pressed={venue === "overview"}
          >
            <span className="venue-tab-title">
              <span className="venue-overview-icon" aria-hidden="true">Σ</span>
              <span>总览</span>
            </span>
            <strong className="venue-equity">{formatAccountValue(overviewEquity)}</strong>
          </button>
          {accounts.map((account) => (
            <button
              key={account.id}
              type="button"
              title={`${account.account_name} · ${account.exchange.toUpperCase()}`}
              aria-label={`${account.account_name}，${account.exchange.toUpperCase()} 账户`}
              onClick={() => {
                if (venue === account.id) return;
                selectVenue(account.id);
              }}
              className={`venue-tab${venue === account.id ? " on" : ""}`}
              aria-pressed={venue === account.id}
            >
              <span className="venue-tab-title">
                <ExchangeIcon exchange={account.exchange} />
                <span>{account.account_name}</span>
              </span>
              <strong className="venue-equity">{formatAccountValue(accountEquities[account.id])}</strong>
            </button>
          ))}
        </div>
        <div className="ticker-health" aria-label="系统状态正常">
          <i className="monitor-dot healthy" aria-hidden="true" />
          <span>正常</span>
        </div>
      </section>

      <section className="workspace" id="live">
        <div className="arena" id="models">
          <div className="chart-wrap">
            <EquityCandlestickChart
              accountId={venue === "overview" ? null : venue}
              accountName={selectedAccount?.account_name ?? "总览"}
            />
          </div>
        </div>
        <aside aria-label="交易详情">
          <div className="tabs" role="tablist" aria-label="交易详情分类">
            {panelNames.map((name) => (
              <button
                key={name}
                type="button"
                role="tab"
                aria-selected={panel === name}
                onClick={() => setPanel(name)}
                className={panel === name ? "on" : ""}
              >
                {name}
              </button>
            ))}
          </div>
          <div className="panel-content" role="tabpanel" ref={panelContentRef}>
            <PanelContent
              panel={panel}
              account={selectedAccount}
              portfolio={portfolio}
              loading={portfolioLoading}
              error={portfolioError}
              overview={venue === "overview"}
              scrollContainerRef={panelContentRef}
            />
          </div>
        </aside>
      </section>

      <div className="chart-status">
        <span>钱包余额 · SQLite 快照</span>
        <span>TRADINGVIEW · UTC</span>
      </div>
    </>
  );
}
