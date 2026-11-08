import test from 'node:test';
import assert from 'node:assert/strict';
import { selectMarkets, compactVolume } from './market-picker.ts';
import type { MarketSummary } from './api.ts';
const rows: MarketSummary[] = [
  {symbol:'ETHUSDC-PERP',base:'ETH',quote:'USDC',last_price:'2000',quote_volume:'2000000',change_percent:'-2',funding_rate:'-0.0001'},
  {symbol:'BTCUSDT-PERP',base:'BTC',quote:'USDT',last_price:'70000',quote_volume:'9000000',change_percent:'3',funding_rate:'0.0001'},
  {symbol:'ETHUSDT-PERP',base:'ETH',quote:'USDT',last_price:null,quote_volume:null,change_percent:null,funding_rate:null},
];
test('picker combines normalized search, settlement and favorites without changing source rows',()=>{
  assert.deepEqual(selectMarkets(rows,' eth/usdc ','',['ETHUSDC-PERP'],true,'symbol',false).map(r=>r.symbol),['ETHUSDC-PERP']);
  assert.equal(selectMarkets(rows,'eth','USDT',[],false,'symbol',false)[0].symbol,'ETHUSDT-PERP');
  assert.equal(selectMarkets(rows,'','',[],true,'symbol',false).length,0);
  assert.equal(rows[0].symbol,'ETHUSDC-PERP');
});
test('numeric sorting handles signed rates and always leaves unavailable data last',()=>{
  assert.equal(selectMarkets(rows,'','',[],false,'funding_rate',false)[0].symbol,'ETHUSDC-PERP');
  assert.equal(selectMarkets(rows,'','',[],false,'quote_volume',true)[0].symbol,'BTCUSDT-PERP');
  assert.equal(selectMarkets(rows,'','',[],false,'last_price',false).at(-1)?.symbol,'ETHUSDT-PERP');
  assert.equal(compactVolume(null),'—'); assert.equal(compactVolume('3700000000'),'3.70B');
});
