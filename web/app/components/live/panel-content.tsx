import type { RefObject } from "react";
import type { ExchangeAccount, ExchangePortfolio } from "../types";
import { AccountPanel } from "./account-panel";
import { PositionsPanel } from "./positions-panel";
import { TradesPanel } from "./trades-panel";

export const panelNames = ["账户", "策略", "操作记录", "仓位"] as const;

export type PanelName = (typeof panelNames)[number];

type PanelContentProps = {
  panel: PanelName;
  account: ExchangeAccount | null;
  portfolio: ExchangePortfolio | null;
  loading: boolean;
  error: string;
  overview: boolean;
  scrollContainerRef: RefObject<HTMLDivElement | null>;
};

function StrategyPanel() {
  return (
    <>
      <h2>Model Chat</h2>
      <p className="log-time">10:42:18 · DEEPSEEK</p>
      <p>
        Momentum remains constructive above the daily VWAP. I&apos;m reducing
        leverage while keeping the long thesis intact.
      </p>
      <p className="log-time">10:39:04 · CLAUDE</p>
      <p>
        Volatility is expanding. No new position until the next confirmation
        candle closes.
      </p>
      <p className="log-time">10:35:51 · GROK</p>
      <p>ETH/BTC relative strength just flipped. Rotating 15% of available margin.</p>
    </>
  );
}

export function PanelContent(props: PanelContentProps) {
  const scopeName = props.overview ? "全部账户" : props.account?.account_name;

  if (props.panel === "策略") {
    return <StrategyPanel />;
  }
  if (props.panel === "操作记录") {
    const scopeKey = props.overview ? "overview" : `account-${props.account?.id ?? "none"}`;
    return <TradesPanel key={scopeKey} {...props} scopeName={scopeName} />;
  }
  if (props.panel === "仓位") {
    return <PositionsPanel {...props} scopeName={scopeName} />;
  }
  return <AccountPanel {...props} scopeName={scopeName} />;
}
