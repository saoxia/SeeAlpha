import test from 'node:test';
import assert from 'node:assert/strict';
import { restoreChartView, restoreTradeSelection } from './preferences.ts';

test('last contract and timeframe restore independently without trusting invalid storage', () => {
  assert.deepEqual(restoreTradeSelection('{"symbol":"ETHUSDC-PERP","interval":"4h"}'), {symbol:'ETHUSDC-PERP',interval:'4h'});
  assert.deepEqual(restoreTradeSelection('{"symbol":"../bad","interval":"4h"}'), {symbol:'BTCUSDT-PERP',interval:'4h'});
  assert.deepEqual(restoreTradeSelection('{"symbol":"ETHUSDT-PERP","interval":"2s"}'), {symbol:'ETHUSDT-PERP',interval:'15m'});
  for (const value of [null, '{', 'null', '[]']) assert.deepEqual(restoreTradeSelection(value), {symbol:'BTCUSDT-PERP',interval:'15m'});
});

test('chart zoom and manual price range round-trip while invalid dimensions fall back safely', () => {
  const view = {barSpacing:12.5,rightOffset:-30.5,priceRange:{from:2100,to:2300}};
  assert.deepEqual(restoreChartView(JSON.stringify(view)), view);
  assert.deepEqual(restoreChartView(JSON.stringify({...view,priceRange:{from:2300,to:2100}})), {...view,priceRange:null});
  assert.deepEqual(restoreChartView(JSON.stringify({...view,priceRange:null})), {...view,priceRange:null});
  for (const value of [null, '{', '{}', '{"barSpacing":0,"rightOffset":0}', '{"barSpacing":10,"rightOffset":"1"}', '{"barSpacing":1e999,"rightOffset":0}']) assert.equal(restoreChartView(value), null);
});
