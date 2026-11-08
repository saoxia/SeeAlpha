import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sma, ema, bollinger, rsi } from './indicators.ts';
test('indicator warmup excludes unavailable values and EMA seeds from SMA', () => {
  assert.deepEqual(sma([1,2,3,4,5],3),[null,null,2,3,4]);
  assert.deepEqual(ema([1,2,3,4,5],3),[null,null,2,3,4]);
  assert.deepEqual(sma([1],3),[null]);
  assert.equal(bollinger(Array(20).fill(5))[19]?.upper,5);
  assert.equal(bollinger([1,2,3],3)[2]?.upper,2+2*Math.sqrt(2/3));
});
test('Wilder RSI handles rising, falling and flat markets', () => {
  assert.equal(rsi(Array.from({length:20},(_,i)=>i))[14],100);
  assert.equal(rsi(Array.from({length:20},(_,i)=>20-i))[14],0);
  assert.equal(rsi(Array(20).fill(5))[14],50);
  const prices=[44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,45.84,46.08,45.89,46.03,45.61,46.28,46.28];
  assert.ok(Math.abs(rsi(prices)[14]!-70.464135)<0.00001);
});

import { defaultIndicators, indicatorError, indicatorPlots, newIndicator, restoreIndicators } from './indicators.ts';
const candles=Array.from({length:12},(_,i)=>({time:i+1,open:String(i+1),high:String(i+3),low:String(i),close:String(i+2),volume:String(10+i)}));
test('independent copies of the same indicator use their own periods, sources and styles',()=>{
  const first={...newIndicator('MA','one'),parameters:[2],width:3 as const,style:2 as const,colors:['#ff0000']};
  const second={...newIndicator('MA','two'),parameters:[3],source:'high' as const};
  const plots=indicatorPlots(candles,[first,second]);
  assert.deepEqual(plots.map(p=>p.pane),[0,0]);
  assert.equal(plots[0].values[1],2.5);assert.equal(plots[1].values[2],4);
  assert.equal(plots[0].width,3);assert.equal(plots[0].style,2);assert.equal(plots[0].color,'#ff0000');
  assert.notEqual(plots[0].key,plots[1].key);
  assert.equal(indicatorPlots(candles,[{...first,visible:false},second]).length,1);
});
test('BOLL multiplier and RSI thresholds are configurable without filling the warmup',()=>{
  const bands=bollinger([1,2,3],3,3);
  assert.equal(bands[2]?.upper,2+3*Math.sqrt(2/3));
  const config={...newIndicator('RSI','rsi'),parameters:[3,80,20]};
  const plots=indicatorPlots(candles,[config]);
  assert.equal(plots[0].values[2],null);assert.equal(plots[0].values[3],100);
  assert.equal(plots[1].values[3],80);assert.equal(plots[2].values[3],20);
});
test('MACD signal starts after both EMA and signal warmup, with Binance histogram scaling',()=>{
  const rows=[1,2,4,8,16].map((v,i)=>({time:i,open:String(v),high:String(v),low:String(v),close:String(v),volume:'1'}));
  const plots=indicatorPlots(rows,[{...newIndicator('MACD','macd'),parameters:[2,3,2]}]);
  assert.equal(plots[0].values[1],null);assert.ok(Math.abs(plots[0].values[2]!-5/6)<1e-10);
  assert.equal(plots[1].values[2],null);assert.ok(Math.abs(plots[1].values[3]!-37/36)<1e-10);
  assert.ok(Math.abs(plots[2].values[3]!-7/18)<1e-10);
  assert.deepEqual(plots.map(p=>p.pane),[1,1,1]);
});
test('saved custom settings round-trip, support empty workspaces and migrate old toggles',()=>{
  const rows=[{...newIndicator('EMA','custom'),parameters:[55],width:4 as const,colors:['#123abc']}];
  assert.deepEqual(restoreIndicators(JSON.parse(JSON.stringify(rows))),rows);
  assert.deepEqual(restoreIndicators([]),[]);
  const migrated=restoreIndicators(null,{MA:false,EMA:true,VOL:false,RSI:true});
  assert.deepEqual(migrated.map(c=>c.kind),['EMA','EMA','EMA','RSI']);
  assert.deepEqual(migrated.slice(0,3).map(c=>c.parameters[0]),[7,25,99]);
  assert.equal(defaultIndicators().length,4);
});
test('invalid periods, MACD/RSI relationships and untrusted persisted styles never reach the chart',()=>{
  const ma=newIndicator('MA','ma');
  for(const value of [0,-1,501,1.5,NaN,Infinity])assert.notEqual(indicatorError({...ma,parameters:[value]}),'');
  assert.notEqual(indicatorError({...newIndicator('MACD','macd'),parameters:[26,12,9]}),'');
  assert.notEqual(indicatorError({...newIndicator('RSI','rsi'),parameters:[14,20,80]}),'');
  for(const changes of [{colors:['red']},{source:'constructor'},{width:9},{kind:'unknown'}]){
    assert.deepEqual(restoreIndicators([{...ma,...changes}]).map(c=>c.kind),['MA','MA','MA','VOL']);
  }
  assert.equal(restoreIndicators([ma,ma]).length,4);
});
test('insufficient history stays empty and separate study instances own separate panes',()=>{
  const configs=[{...newIndicator('EMA','long'),parameters:[500]},newIndicator('RSI','first'),newIndicator('RSI','second')];
  const plots=indicatorPlots(candles,configs);
  assert.ok(plots[0].values.every(v=>v===null));
  assert.deepEqual([...new Set(plots.map(p=>p.pane))],[0,1,2]);
  assert.deepEqual(indicatorPlots([],configs).map(p=>p.values.length),Array(7).fill(0));
});


test('stored indicator preferences survive reload with malformed display options and apply across candle datasets',()=>{
  const configs=[
    {...newIndicator('EMA','saved-ema'),parameters:[3],source:'high' as const,width:3 as const,style:2 as const,colors:['#123abc']},
    {...newIndicator('RSI','saved-rsi'),parameters:[4,85,15],visible:false},
  ];
  const stored=JSON.stringify(configs);
  const restored=restoreIndicators(stored,'broken display JSON');
  assert.deepEqual(restored,configs);
  assert.deepEqual(restoreIndicators(stored),configs);
  const otherSymbol=candles.map(row=>({...row,high:String(Number(row.high)*2)}));
  assert.equal(indicatorPlots(candles,restored)[0].title,'EMA(3)');
  assert.equal(indicatorPlots(otherSymbol,restored)[0].title,'EMA(3)');
  assert.equal(indicatorPlots(otherSymbol,restored)[0].width,3);
  assert.deepEqual(restoreIndicators('[]','broken display JSON'),[]);
  assert.deepEqual(restoreIndicators('broken indicator JSON','{"MA":false,"VOL":true}').map(c=>c.kind),['VOL']);
});
