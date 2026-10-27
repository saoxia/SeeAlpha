import type { ExchangeAccountSummary } from "@/app/components/types";

export function formatAccountValue(value: string | null | undefined) {
  if (!value) return "$ --";
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "$ --";
  return `$ ${amount.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function hasValidAccountValueInvariant(
  summary: Pick<ExchangeAccountSummary, "wallet_balance" | "unrealized_pnl" | "total_equity">,
) {
  const walletBalance = Number(summary.wallet_balance);
  const unrealizedPnl = Number(summary.unrealized_pnl);
  const totalEquity = Number(summary.total_equity);

  if (![walletBalance, unrealizedPnl, totalEquity].every(Number.isFinite)) return false;
  return Math.abs(walletBalance + unrealizedPnl - totalEquity) < 0.000001;
}
