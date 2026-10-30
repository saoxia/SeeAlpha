import type { ExchangeTrade } from '../../components/types';

export type OperationPage = {
  records: ExchangeTrade[];
  next_cursor: string | null;
  has_more: boolean;
  warnings: string[];
  sync: Array<{ account_id: number; status: string; complete: boolean; checked_at: number; message: string }>;
};

export const operationLabels: Record<ExchangeTrade['event_type'], string> = {
  place: '下单', amend: '改单', cancel: '撤单', fill: '成交',
  reject: '拒绝', expire: '过期', correction: '对账修正', cashflow: '资金变动',
};

export function cashflowLabel(incomeType?: string | null): string {
  const labels: Record<string, string> = {
    TRANSFER: '资金划转', FUNDING_FEE: '资金费', COMMISSION: '手续费',
    REALIZED_PNL: '已实现盈亏', INSURANCE_CLEAR: '保险清算',
    COMMISSION_REBATE: '手续费返还', REFERRAL_KICKBACK: '推荐返佣',
  };
  return incomeType ? (labels[incomeType] ?? incomeType) : '资金变动';
}

export function operationSourceLabel(operation: Pick<ExchangeTrade, 'source' | 'strategy_id'> & { event_type?: ExchangeTrade['event_type'] }): string {
  if (operation.event_type === 'cashflow') return '交易所资金流水';
  if (operation.source === 'project') return operation.strategy_id ? `项目策略 · ${operation.strategy_id}` : '项目操作';
  if (operation.source === 'reconciliation') return '对账修正（非真实成交）';
  return '外部操作（来源待确认）';
}

export function operationsPath(accountId: number | null, cursor?: string | null, limit = 50): string {
  const query = new URLSearchParams({ limit: String(limit) });
  if (accountId !== null) query.set('account_id', String(accountId));
  if (cursor) query.set('cursor', cursor);
  return `/api/operations?${query}`;
}

export function mergeLatestOperations(current: ExchangeTrade[], page: OperationPage): ExchangeTrade[] {
  const last = page.records.at(-1);
  const older = page.has_more && last
    ? current.filter((record) => (record.timestamp ?? 0) < (last.timestamp ?? 0)
      || (record.timestamp === last.timestamp && record.id < last.id))
    : [];
  const records = new Map([...older, ...page.records].map((record) => [record.id, record]));
  return [...records.values()].sort((left, right) => (left.timestamp ?? 0) - (right.timestamp ?? 0) || left.id.localeCompare(right.id));
}
