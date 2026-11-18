import type { IChartApi, ISeriesApi } from "lightweight-charts";
import type { Market } from "@/app/features/trade/api";
import { type IndicatorConfig } from "@/app/features/trade/indicators";
import { type DepthSnapshot, type LiquidationEstimates, verifiedLiquidationEstimates, volumeProfile } from "@/app/features/trade/market-overlays";

type PlotState={
  rows:Market["candles"];
  studies:IndicatorConfig[];
  depths:readonly DepthSnapshot[];
  liquidations:LiquidationEstimates|null;
  symbol:string;
  now:number;
};

/** A passive overlay on the *existing* TradingView Lightweight Charts price pane.
 * It never captures mouse input or replaces chart series/scale ownership. */
export class TradingMarketOverlays {
  private readonly canvas:HTMLCanvasElement;
  private readonly resize:ResizeObserver;
  private frame:number|null=null;
  private disposed=false;
  private state:PlotState={rows:[],studies:[],depths:[],liquidations:null,symbol:"",now:0};

  constructor(
    private readonly host:HTMLElement,
    private readonly chart:IChartApi,
    private readonly series:ISeriesApi<"Candlestick">,
  ){
    const canvas=document.createElement("canvas");
    canvas.className="trade-market-overlay-canvas";
    canvas.setAttribute("aria-hidden","true");
    this.canvas=canvas;
    host.append(canvas);
    this.resize=new ResizeObserver(()=>this.schedule());
    this.resize.observe(host);
  }

  update(value:PlotState):void{
    if(this.disposed)return;
    this.state=value;
    this.schedule();
  }

  schedule():void{
    if(this.disposed||this.frame!==null)return;
    this.frame=requestAnimationFrame(()=>{this.frame=null;this.draw();});
  }

  private draw():void{
    if(this.disposed)return;
    const bounds=this.host.getBoundingClientRect();
    const width=bounds.width,height=bounds.height;
    if(width<=0||height<=0)return;
    const dpr=window.devicePixelRatio||1;
    const pixelWidth=Math.round(width*dpr),pixelHeight=Math.round(height*dpr);
    if(this.canvas.width!==pixelWidth||this.canvas.height!==pixelHeight){
      this.canvas.width=pixelWidth;this.canvas.height=pixelHeight;
    }
    const ctx=this.canvas.getContext("2d");
    if(!ctx)return;
    ctx.setTransform(dpr,0,0,dpr,0,0);
    ctx.clearRect(0,0,width,height);
    const chartWidth=Math.min(width,this.chart.timeScale().width());
    const chartHeight=this.chart.panes()[0]?.getHeight()??0;
    if(chartWidth<=0||chartHeight<=0)return;
    ctx.save();
    ctx.beginPath();ctx.rect(0,0,chartWidth,chartHeight);ctx.clip();
    const vp=this.state.studies.find(item=>item.kind==="VP"&&item.visible);
    const book=this.state.studies.find(item=>item.kind==="BOOK"&&item.visible);
    const liq=this.state.studies.find(item=>item.kind==="LIQ"&&item.visible);
    if(book)this.drawBook(ctx,chartWidth,book);
    if(liq)this.drawLiquidation(ctx,chartWidth,liq);
    if(vp)this.drawProfile(ctx,chartWidth,vp);
    ctx.restore();
  }

  private coordinate(price:number):number|null {
    return this.series.priceToCoordinate(price);
  }

  private drawProfile(ctx:CanvasRenderingContext2D,width:number,config:IndicatorConfig):void {
    const bars=this.state.rows;
    if(!bars.length)return;
    const visible=this.chart.timeScale().getVisibleLogicalRange();
    const from=visible?Math.max(0,Math.floor(visible.from)):0;
    const to=visible?Math.min(bars.length,Math.ceil(visible.to)+1):bars.length;
    const profile=volumeProfile(from<to?bars.slice(from,to):[],config.parameters[0],config.parameters[1]);
    if(!profile)return;
    const maxVolume=profile.rows.reduce((max,row)=>Math.max(max,row.volume),0);
    if(maxVolume<=0)return;
    const maxWidth=Math.min(config.parameters[2],width*.28);
    for(const row of profile.rows){
      if(row.volume<=0)continue;
      const a=this.coordinate(row.high),b=this.coordinate(row.low);
      if(a===null||b===null)continue;
      const xWidth=maxWidth*row.volume/maxVolume;
      const inValue=row.high<=profile.vah&&row.low>=profile.val;
      ctx.globalAlpha=inValue ? 0.37 : 0.2;
      ctx.fillStyle=config.colors[0];
      ctx.fillRect(width-xWidth,Math.min(a,b)+.5,xWidth,Math.max(1,Math.abs(a-b)-1));
    }
    ctx.globalAlpha=1;
    for(const [price,label,color,style] of [
      [profile.poc,"POC",config.colors[1],[]],
      [profile.vah,"VAH",config.colors[2],[4,4]],
      [profile.val,"VAL",config.colors[2],[4,4]],
    ] as Array<[number,string,string,number[]]>){
      const y=this.coordinate(price);
      if(y===null)continue;
      ctx.beginPath();ctx.strokeStyle=color;ctx.lineWidth=1;ctx.setLineDash(style);
      ctx.moveTo(0,Math.round(y)+.5);ctx.lineTo(width,Math.round(y)+.5);ctx.stroke();ctx.setLineDash([]);
      ctx.fillStyle=color;ctx.font="10px sans-serif";ctx.fillText(label,6,Math.max(10,y-3));
    }
    ctx.fillStyle="#71849b";ctx.font="10px sans-serif";
    ctx.fillText("VP · OHLCV估算",Math.max(0,width-maxWidth),12);
  }

  private drawBook(ctx:CanvasRenderingContext2D,width:number,config:IndicatorConfig):void {
    const keepMs=config.parameters[0]*1000;
    const latest=this.state.depths.at(-1)?.time;
    if(!latest)return;
    const samples=this.state.depths.filter(s=>s.time>=latest-keepMs);
    if(!samples.length)return;
    let maxSize=0;
    for(const snapshot of samples)for(const level of snapshot.levels)maxSize=Math.max(maxSize,level.quantity);
    if(!maxSize)return;
    // This is an explicitly independent sub-candle observation window. A 100ms
    // depth snapshot cannot be placed accurately on a 1m–1d candle time axis.
    const xLeft=width*.40,xSpan=width-xLeft;
    const begin=samples[0].time,range=Math.max(1000,latest-begin);
    ctx.fillStyle="#738497";ctx.font="10px sans-serif";
    ctx.fillText("20档盘口 · 页面内采样（独立时间窗）",10,25);
    for(let i=0;i<samples.length;i++){
      const snapshot=samples[i];
      const x0=xLeft+(snapshot.time-begin)/range*xSpan;
      const x1=i+1<samples.length?xLeft+(samples[i+1].time-begin)/range*xSpan:width;
      if(x1<=x0||x1<0||x0>width)continue;
      for(const level of snapshot.levels){
        const y=this.coordinate(level.price);if(y===null)continue;
        ctx.fillStyle=level.side==="bid"?config.colors[0]:config.colors[1];
        ctx.globalAlpha=(config.parameters[1]/100)*Math.sqrt(level.quantity/maxSize);
        ctx.fillRect(x0,y-2.5,Math.max(1,x1-x0),5);
      }
    }
    ctx.globalAlpha=1;
  }

  private drawLiquidation(ctx:CanvasRenderingContext2D,width:number,config:IndicatorConfig):void {
    const source=verifiedLiquidationEstimates(this.state.liquidations,this.state.symbol,this.state.now);
    if(!source){
      ctx.fillStyle="#8d7284";ctx.font="11px sans-serif";
      ctx.fillText("预估强平：暂无可信模型数据",10,41);
      return;
    }
    const max=source.levels.reduce((value,level)=>Math.max(value,level.notional),0);
    const maxWidth=Math.min(220,width*.35);
    for(const level of source.levels){
      const y=this.coordinate(level.price);
      if(y===null)continue;
      ctx.fillStyle=level.side==="long"?config.colors[0]:config.colors[1];
      ctx.globalAlpha=config.parameters[0]/100;
      const size=maxWidth*level.notional/max;
      ctx.fillRect(width-size,y-3,size,6);
    }
    ctx.globalAlpha=1;ctx.fillStyle="#9b6484";ctx.font="10px sans-serif";
    ctx.fillText("强平模型估算 · "+source.source,10,41);
  }

  remove():void{
    if(this.disposed)return;this.disposed=true;
    this.resize.disconnect();
    if(this.frame!==null)cancelAnimationFrame(this.frame);
    this.frame=null;this.canvas.remove();
  }
}
