/**
 * Chart-only signal projection. This module is not connected to order placement,
 * strategy publication or broker execution. Callers must supply provenance.
 */
import { ema } from "./indicators";

export type ChartSignal = {
  id:string;
  time:number;
  direction:"buy"|"sell";
  label:string;
  source:"chart"|"strategy";
};
type CloseBar = {time:number;close:string};
export function crossoverSignals(bars:readonly CloseBar[],fastPeriod:number,slowPeriod:number):ChartSignal[] {
  if(!Number.isInteger(fastPeriod)||!Number.isInteger(slowPeriod)||fastPeriod<1||slowPeriod>500||fastPeriod>=slowPeriod)return [];
  const closes=bars.map(b=>Number(b.close));
  if(closes.some(v=>!Number.isFinite(v)))return [];
  const fast=ema(closes,fastPeriod),slow=ema(closes,slowPeriod);
  const results:ChartSignal[]=[];
  for(let i=1;i<bars.length;i++){
    if(fast[i]===null||slow[i]===null||fast[i-1]===null||slow[i-1]===null)continue;
    const up=fast[i-1]!<=slow[i-1]!&&fast[i]!>slow[i]!;
    const down=fast[i-1]!>=slow[i-1]!&&fast[i]!<slow[i]!;
    if(!up&&!down)continue;
    results.push({id:`crossover:${bars[i].time}:${up?"buy":"sell"}`,time:bars[i].time,direction:up?"buy":"sell",label:up?"金叉":"死叉",source:"chart"});
  }
  return results.slice(-500);
}
export function sanitizeChartSignals(source:unknown):ChartSignal[] {
  if(!Array.isArray(source))return [];
  const seen=new Set<string>();
  const result:ChartSignal[]=[];
  for(const item of source.slice(-2000)){
    if(!item||typeof item!=="object"||typeof item.id!=="string"||item.id.length>80||seen.has(item.id)||
      typeof item.time!=="number"||!Number.isSafeInteger(item.time)||item.time<=0||
      (item.direction!=="buy"&&item.direction!=="sell")||
      (item.source!=="chart"&&item.source!=="strategy")||
      typeof item.label!=="string"||item.label.length>30)continue;
    seen.add(item.id);
    result.push({id:item.id,time:item.time,direction:item.direction,source:item.source,label:item.label});
  }
  return result.sort((a,b)=>a.time-b.time).slice(-500);
}
