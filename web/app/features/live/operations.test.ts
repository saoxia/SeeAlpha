import assert from 'node:assert/strict';
import test from 'node:test';
import { cashflowLabel, operationsPath, operationSourceLabel, mergeLatestOperations, type OperationPage } from './operations.ts';
import type { ExchangeTrade } from '../../components/types.ts';

const row = (id: string, timestamp: number, source = 'external') => ({ id, timestamp, source } as ExchangeTrade);
const page = (records: ExchangeTrade[], has_more = false): OperationPage => ({ records, has_more, next_cursor: null, warnings: [], sync: [] });

test('all-account and account scopes use project ledger and opaque cursor', () => {
  assert.equal(operationsPath(null), '/api/operations?limit=50');
  assert.equal(operationsPath(7, 'cursor+/='), '/api/operations?limit=50&account_id=7&cursor=cursor%2B%2F%3D');
});

test('external orders are never labelled manual and corrections are separate', () => {
  assert.match(operationSourceLabel(row('a', 1)), /外部/);
  assert.match(operationSourceLabel(row('a', 1, 'reconciliation')), /非真实成交/);
  assert.equal(operationSourceLabel({source: 'project', strategy_id: 's1'}), '项目策略 · s1');
});

test('cashflows retain their venue category without becoming trades or strategy operations', () => {
  assert.equal(cashflowLabel('FUNDING_FEE'), '资金费');
  assert.equal(cashflowLabel('NEW_VENUE_TYPE'), 'NEW_VENUE_TYPE');
  assert.equal(cashflowLabel('OPTIONS_FEE'), '期权手续费');
  assert.equal(cashflowLabel('OPTIONS_EXERCISE_PNL'), '期权行权盈亏');
  assert.equal(operationSourceLabel({ source: 'external', event_type: 'cashflow' }), '交易所资金流水');
});

test('polling replaces promoted records and removes merged identities while keeping older pages', () => {
  const result = mergeLatestOperations([row('old', 1), row('duplicate', 3), row('canonical', 3)], page([row('canonical', 3, 'project'), row('boundary', 2)], true));
  assert.deepEqual(result.map((r) => r.id), ['old', 'boundary', 'canonical']);
  assert.equal(result[2].source, 'project');
  assert.deepEqual(mergeLatestOperations(result, page([])), []);
});
