import { test } from "node:test";
import assert from "node:assert/strict";
import { volumeProfile, appendDepthSnapshot, verifiedLiquidationEstimates } from "./market-overlays.ts";
import { indicatorPlots, isMarketOverlay, newIndicator, restoreIndicators } from "./indicators.ts";

const candle=(time:number,low:string,high:string,volume:string)=>({
  time,open:low,high,low,close:high,volume,
});

test("OHLCV profile conserves volume and derives a bounded value area around POC",()=>{
  const result=volumeProfile([
    candle(1,"100","102","80"),
    candle(2,"101","103","20"),
    candle(3,"102","103","10"),
  ],8,70);
  assert.ok(result);
  assert.equal(result.source,"ohlcv-uniform-estimate");
  assert.equal(result.rows.length,8);
  assert.ok(Math.abs(result.volume-110)<1e-9);
  assert.ok(Math.abs(result.rows.reduce((sum,row)=>sum+row.volume,0)-110)<1e-9);
  assert.ok(result.poc>=result.val&&result.poc<=result.vah);
  assert.ok(result.rows.some(row=>row.volume>0));
});

test("flat-price profiles and missing volumes stay numerically sound",()=>{
  const flat=volumeProfile([candle(1,"100","100","20")]);
  assert.deepEqual(flat?.rows,[{low:100,high:100,volume:20}]);
  assert.equal(volumeProfile([candle(1,"100","102","0")]),null);
  assert.equal(volumeProfile([],64),null);
  assert.throws(()=>volumeProfile([],2),RangeError);
  assert.throws(()=>volumeProfile([],48,101),RangeError);
});

test("depth snapshots use only observed book levels, skip duplicates and bound history",()=>{
  const depth=(time:number)=>({depth_time:time,bids:[["100","2"],["99","0"],["oops","5"]],asks:[["101","3"]]});
  const start=appendDepthSnapshot([],depth(10000),2,1000);
  assert.equal(start.length,1);
  assert.deepEqual(start[0].levels,[
    {price:100,quantity:2,side:"bid"},
    {price:101,quantity:3,side:"ask"},
  ]);
  assert.equal(appendDepthSnapshot(start,depth(10500),2,1000).length,1);
  const next=appendDepthSnapshot(start,depth(11100),2,1000);
  assert.equal(next.length,2);
  assert.deepEqual(appendDepthSnapshot(next,depth(12000),2,1000).map(row=>row.time),[10000,11100]);
  assert.deepEqual(appendDepthSnapshot(next,depth(12500),2,1000).map(row=>row.time),[11100,12500]);
  assert.deepEqual(appendDepthSnapshot([],null),[]);
});

test("market-level liquidation estimates must include current source, market and nonnegative model levels",()=>{
  const now=1000000;
  const value={time:now,symbol:"BTCUSDT-PERP",source:"verified-external-model",levels:[
    {price:90000,notional:2000,side:"long" as const},
    {price:92000,notional:1000,side:"short" as const},
  ]};
  const verified=verifiedLiquidationEstimates(value,value.symbol,now);
  assert.deepEqual(verified,value);
  assert.notStrictEqual(verified?.levels,value.levels);
  assert.equal(verifiedLiquidationEstimates(value,"ETHUSDT-PERP",now),null);
  assert.equal(verifiedLiquidationEstimates({...value,time:now-61000},value.symbol,now),null);
  assert.equal(verifiedLiquidationEstimates({...value,levels:[{price:0,notional:1,side:"short" as const}]},value.symbol,now),null);
  assert.equal(verifiedLiquidationEstimates(null,value.symbol,now),null);
});

test("new overlays use existing technical-indicator catalog, persistence and main price pane",()=>{
  const overlays=["VP","BOOK","LIQ"] as const;
  const configs=overlays.map((kind,i)=>newIndicator(kind,"overlay-"+i));
  assert.ok(configs.every(config=>isMarketOverlay(config.kind)));
  assert.deepEqual(restoreIndicators(JSON.stringify(configs)),configs);
  const candles=[candle(1,"100","101","10"),candle(2,"101","102","15")];
  assert.equal(indicatorPlots(candles,configs).length,0);
  assert.equal(indicatorPlots(candles,[newIndicator("MA","normal"),...configs]).length,1);
});
