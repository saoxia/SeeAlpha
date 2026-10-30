import { ExchangeIcon } from "../brand/exchange-icon";
import type { ExchangeAccount, ExchangeTrade } from "../types";
import { cashflowLabel, operationLabels, operationSourceLabel } from "@/app/features/live/operations";

function formatTime(value: number) { return new Date(value).toLocaleString("zh-CN", { hour12: false }); }
function formatNumber(value: string | null, digits = 2) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }) : "--";
}
function AccountIdentity({ exchange, name }: { exchange: ExchangeAccount["exchange"]; name: string }) {
  return <span className="data-account-identity"><ExchangeIcon exchange={exchange} /><b>{name}</b></span>;
}

export function OperationsList({ operations, loading }: { operations: ExchangeTrade[]; loading: boolean }) {
  if (loading && !operations.length) return <p className="data-empty">正在读取账户记录…</p>;
  if (!operations.length) return <p className="data-empty">当前筛选范围内没有账户记录。</p>;
  return <div className="data-record-list">
    <div className="data-record-head"><span>账户 / 时间</span><span>操作</span><span>数量 / 价格</span><span>状态</span></div>
    {operations.map((item) => <div className="data-record-row" key={item.id}>
      <div><AccountIdentity exchange={item.exchange} name={item.account_name} /><small>{formatTime(item.timestamp || 0)}</small></div>
      <div><b>{item.event_type === "cashflow" ? cashflowLabel(item.income_type) : operationLabels[item.event_type]}</b><small>{item.symbol || "账户资金"}{item.event_type !== "cashflow" && ` · ${item.side || "--"}`}</small></div>
      <div><b>{item.event_type === "cashflow" ? `${item.amount} ${item.asset || ""}` : formatNumber(item.amount, 4)}</b><small>{item.event_type === "cashflow" ? `流水号 ${item.transaction_id || "--"}` : `@ ${formatNumber(item.price, 4)}`}</small></div>
      <div><b>{item.status || "--"}</b><small>{operationSourceLabel(item)}</small></div>
    </div>)}
  </div>;
}

