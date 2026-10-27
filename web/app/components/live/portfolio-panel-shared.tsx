import type { ExchangeAccount } from "../types";

export type PortfolioPanelStateProps = {
  account: ExchangeAccount | null;
  loading: boolean;
  error: string;
  overview: boolean;
};

export function decimal(value: string | null, maximumFractionDigits = 8) {
  if (value === null || value === "") return "--";
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return value;
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits }).format(parsed);
}

export function fixedDecimal(value: string | null, fractionDigits: number) {
  if (value === null || value === "") return "--";
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return value;
  return new Intl.NumberFormat("zh-CN", {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(parsed);
}

export function PortfolioPanelState({ account, loading, error, overview }: PortfolioPanelStateProps) {
  if (!account && !overview) return <p className="portfolio-state">请选择一个账户查看实时账户数据。</p>;
  if (loading) return <p className="portfolio-state">{overview ? "正在汇总全部账户…" : `正在读取 ${account?.account_name}…`}</p>;
  if (error) return <p className="portfolio-state error">{error}</p>;
  return null;
}
