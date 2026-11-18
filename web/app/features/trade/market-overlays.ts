import type { Market } from "./api.ts";

/** The current trading screen uses TradingView Lightweight Charts, not Advanced Charts. */
export type DepthLevel = {price:number; quantity:number; side:"bid"|"ask"};
export type DepthSnapshot = {time:number; levels:DepthLevel[]};
export type LiquidationEstimate = {price:number; notional:number; side:"long"|"short"};
export type LiquidationEstimates = {
  /** Epoch milliseconds, not the candle's UTC-seconds time. */
  time:number;
  symbol:string;
  source:string;
  levels:LiquidationEstimate[];
};
export type ProfileBin = {low:number; high:number; volume:number};
export type VolumeProfile = {rows:ProfileBin[]; poc:number; vah:number; val:number; volume:number; source:"ohlcv-uniform-estimate"};

type Candle = Market["candles"][number];

/** Approximation: OHLCV does not contain trade-at-price execution data. */
export function volumeProfile(candles:readonly Candle[], bins=48, valueAreaPercent=70):VolumeProfile|null {
  if(!Number.isInteger(bins)||bins<8||bins>128||!Number.isFinite(valueAreaPercent)||valueAreaPercent<1||valueAreaPercent>100) {
    throw new RangeError("Invalid Volume Profile settings");
  }
  let bottom=Infinity, top=-Infinity, total=0;
  const bars:{low:number;high:number;volume:number}[]=[];
  for(const row of candles){
    const low=Number(row.low),high=Number(row.high),volume=Number(row.volume);
    if(!Number.isFinite(low)||!Number.isFinite(high)||!Number.isFinite(volume)||high<low||low<=0||volume<=0)continue;
    bottom=Math.min(bottom,low);top=Math.max(top,high);
    bars.push({low,high,volume});total+=volume;
  }
  if(!bars.length||!Number.isFinite(total))return null;
  if(top===bottom)return {rows:[{low:bottom,high:top,volume:total}],poc:bottom,vah:top,val:bottom,volume:total,source:"ohlcv-uniform-estimate"};
  const delta=(top-bottom)/bins;
  const values=new Float64Array(bins);
  for(const bar of bars){
    if(bar.low===bar.high){
      const i=Math.max(0,Math.min(bins-1,Math.floor((bar.low-bottom)/delta)));
      values[i]+=bar.volume;continue;
    }
    const a=Math.max(0,Math.min(bins-1,Math.floor((bar.low-bottom)/delta)));
    const b=Math.max(0,Math.min(bins-1,Math.ceil((bar.high-bottom)/delta)-1));
    for(let i=a;i<=b;i++){
      const overlap=Math.max(0,Math.min(bar.high,bottom+(i+1)*delta)-Math.max(bar.low,bottom+i*delta));
      values[i]+=bar.volume*overlap/(bar.high-bar.low);
    }
  }
  const rows=Array.from(values,(volume,i)=>({low:bottom+i*delta,high:i===bins-1?top:bottom+(i+1)*delta,volume}));
  let peak=0;for(let i=1;i<rows.length;i++)if(rows[i].volume>rows[peak].volume)peak=i;
  let left=peak,right=peak,area=rows[peak].volume;
  while(area<total*valueAreaPercent/100&&(left>0||right<rows.length-1)){
    const a=left>0?rows[left-1].volume:-1;
    const b=right<rows.length-1?rows[right+1].volume:-1;
    if(b>a)area+=rows[++right].volume;else area+=rows[--left].volume;
  }
  return {rows,poc:(rows[peak].low+rows[peak].high)/2,val:rows[left].low,vah:rows[right].high,
    volume:total,source:"ohlcv-uniform-estimate"};
}

/** Record only actually observed 20-level Binance book snapshots, never synthesize history.
 * Retains a short, in-memory window to bound rendering and memory use. */
export function appendDepthSnapshot(
  previous:readonly DepthSnapshot[], observation:Pick<Market,"bids"|"asks"|"depth_time">|null,
  maximum=180,minSpacingMs=1000,
):DepthSnapshot[] {
  const time=observation?.depth_time;
  if(!Number.isSafeInteger(time)||time===null||time===undefined||time<=0)return [...previous];
  if(previous.length&&time-previous[previous.length-1].time<minSpacingMs)return [...previous];
  const levels:DepthLevel[]=[];
  for(const [side,entries] of [["bid",observation.bids],["ask",observation.asks]] as const){
    for(const entry of entries.slice(0,20)){
      const price=Number(entry[0]),quantity=Number(entry[1]);
      if(Number.isFinite(price)&&price>0&&Number.isFinite(quantity)&&quantity>0){
        levels.push({price,quantity,side});
      }
    }
  }
  if(!levels.length)return [...previous];
  const snapshot={time,levels};
  return [...previous,snapshot].filter(row=>row.time>=time-10*60*1000).slice(-Math.max(1,Math.min(300,maximum)));
}

/** Protects chart data provenance: upstream must supply actual modeled level data.
 * Open interest alone cannot determine liquidation price distribution. */
export function verifiedLiquidationEstimates(
  input:LiquidationEstimates|null|undefined,
  symbol:string,now:number,
):LiquidationEstimates|null {
  if(!input||input.symbol!==symbol||!input.source.trim()||!Number.isSafeInteger(input.time)||
    input.time>now+5000||now-input.time>60000||!Array.isArray(input.levels)||input.levels.length>300)return null;
  const levels:LiquidationEstimate[]=[];
  for(const level of input.levels){
    if(!level||!Number.isFinite(level.price)||level.price<=0||
      !Number.isFinite(level.notional)||level.notional<=0||
      (level.side!=="long"&&level.side!=="short"))return null;
    levels.push({price:level.price,notional:level.notional,side:level.side});
  }
  return levels.length?{time:input.time,symbol,source:input.source,levels}:null;
}
