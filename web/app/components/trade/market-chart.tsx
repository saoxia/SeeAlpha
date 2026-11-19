"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { CandlestickSeries, ColorType, HistogramSeries, LineSeries, createChart, createSeriesMarkers, type ISeriesApi, type SeriesMarker, type Time, type UTCTimestamp } from "lightweight-charts";
import type { Market, TradeState } from "@/app/features/trade/api";
import { indicatorPlots, restoreIndicators, studyVisibleForInterval, type IndicatorConfig } from "@/app/features/trade/indicators";
import { chartViewPrefix, restoreChartView } from "@/app/features/trade/preferences";
import { IndicatorEditor, IndicatorLibrary } from "./indicator-settings";
import { emptyStudyWorkspace, restoreStudyWorkspace, workspaceKey, type StudyWorkspace } from "@/app/features/trade/study-workspace";
import { crossoverSignals, sanitizeChartSignals, type ChartSignal } from "@/app/features/trade/chart-signals";
import { TradingMarketOverlays } from "./market-overlays-canvas";
import { appendDepthSnapshot, type DepthSnapshot, type LiquidationEstimates } from "@/app/features/trade/market-overlays";

const defaults = {orders: true, positions: true, history: false, breakeven: false, liquidation: false, preview: true, alerts: false, quick: false};
type Options = typeof defaults;
const displays = {quick: "快捷填价（点击图表）", orders: "当前委托", positions: "持有仓位", history: "历史委托（委托价）", breakeven: "损益两平价", liquidation: "账户持仓强平价", alerts: "价格提醒", preview: "订单预览线"} as const;
export function MarketChart({ market, depth, modeledLiquidations = null, strategySignals, interval, identity, state, preview, onPrice, onNotice, displayDigits, periods, toolbar }: {
  periods: ReactNode; toolbar: ReactNode; displayDigits: number; interval:string;
  /** Read-only strategy/backtest markers; never used for trade commands. */
  strategySignals?: readonly ChartSignal[];
  market: Pick<Market, "candles" | "last_price" | "symbol"> | null;
  /** Already-subscribed Binance 20-level display depth (never sent to order execution). */
  depth: Pick<Market, "bids" | "asks" | "depth_time"> | null;
  /** Optional, separately sourced market-level *modeled* distribution; absent by default. */
  modeledLiquidations?: LiquidationEstimates | null;
  identity: string; state: TradeState | null; preview: string; onPrice: (price: string) => void; onNotice: (message: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const candles = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const chart = useRef<ReturnType<typeof createChart> | null>(null);
  const fitted = useRef("");
  const activeView = useRef<{identity: string} | null>(null);
  const saveView = useRef<() => void>(() => {});
  const nativePrecision = useRef(2);
  const previousCandles = useRef<{identity: string; rows: Market["candles"]}>({identity: "", rows: []});
  const rows = market?.candles;
  const [options, setOptions] = useState<Options>(defaults);
  const [studies, setStudies] = useState<IndicatorConfig[] | null>(null);
  const [workspace,setWorkspace]=useState<StudyWorkspace>(emptyStudyWorkspace);
  const [editingStudyId,setEditingStudyId]=useState<string|null>(null);
  const activeStudies=useMemo(()=>studies?.filter(s=>studyVisibleForInterval(s,interval))??[],[studies,interval]);
  const editingStudy=studies?.find(s=>s.id===editingStudyId);
  const plots = useRef(new Map<string, ISeriesApi<"Line"> | ISeriesApi<"Histogram">>());
  const marketOverlays = useRef<TradingMarketOverlays | null>(null);
  const signalMarkers=useRef<{setMarkers:(markers:SeriesMarker<Time>[])=>void}|null>(null);
  const depthHistory = useRef<DepthSnapshot[]>([]);
  const depthIdentity = useRef("");

  const [menu, setMenu] = useState<"indicators" | "display" | null>(null);
  const [alertPrice, setAlertPrice] = useState("");
  const [armed, setArmed] = useState<number | null>(null);
  const previous = useRef<number | null>(null);
  const handlers = useRef({onPrice, quick: options.quick});
  useEffect(() => {handlers.current = {onPrice, quick: options.quick};}, [onPrice, options.quick]);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      try {
        const saved = localStorage.getItem("seealpha.chart.options");
        setStudies(restoreIndicators(localStorage.getItem("seealpha.chart.indicators"), saved));
        setWorkspace(restoreStudyWorkspace(localStorage.getItem(workspaceKey)));
        const display = JSON.parse(saved ?? "{}");
        setOptions(Object.fromEntries(Object.entries(defaults).map(([key,value]) => [key, typeof display?.[key] === "boolean" ? display[key] : value])) as Options);
      } catch {
        // Storage can be unavailable; preserve any indicators already restored.
        setStudies(current => current ?? restoreIndicators(null));
      }
    },0);
    return () => window.clearTimeout(timer);
  }, []);
  function updateStudies(value:IndicatorConfig[]) {
    setStudies(value);
    try {localStorage.setItem("seealpha.chart.indicators",JSON.stringify(value));} catch { /* Chart preferences remain usable without storage. */ }
  }
  function updateWorkspace(value:StudyWorkspace){setWorkspace(value);try{localStorage.setItem(workspaceKey,JSON.stringify(value));}catch{/* Local preferences remain usable without storage. */}}
  function moveStudy(index:number,direction:number){if(!studies)return;const next=[...studies];const other=index+direction;if(other<0||other>=next.length)return;[next[index],next[other]]=[next[other],next[index]];updateStudies(next);}
  function toggleStudy(id:string){if(studies)updateStudies(studies.map(s=>s.id===id?{...s,visible:!s.visible}:s));}
  function removeStudy(id:string){if(studies)updateStudies(studies.filter(s=>s.id!==id));}
  function applyStudy(config:IndicatorConfig,weight:number){if(studies)updateStudies(studies.map(s=>s.id===config.id?config:s));updateWorkspace({...workspace,paneWeights:{...workspace.paneWeights,[config.id]:weight}});setEditingStudyId(null);}
  function toggle(key: keyof Options) {
    const next = {...options, [key]: !options[key]}; setOptions(next);
    try {localStorage.setItem("seealpha.chart.options",JSON.stringify(next));} catch { /* Display preferences are optional. */ }
  }
  useEffect(() => {
    if (!container.current) return;
    const value = createChart(container.current, {
      autoSize: true, layout: {background: {type: ColorType.Solid, color: "#ffffff"}, textColor: "#808080", fontSize: 11, attributionLogo: true},
      grid: {vertLines: {color: "#f3f3f3"}, horzLines: {color: "#f3f3f3"}},
      rightPriceScale: {borderColor: "#eeeeee", scaleMargins: {top: 0.12, bottom: 0.1}},
      timeScale: {borderColor: "#eeeeee", timeVisible: true},
    });
    chart.current = value;
    candles.current = value.addSeries(CandlestickSeries, {upColor: "#2ebd85", downColor: "#f6465d", borderVisible: false, wickUpColor: "#2ebd85", wickDownColor: "#f6465d"});
    signalMarkers.current=createSeriesMarkers(candles.current,[]);
    marketOverlays.current = new TradingMarketOverlays(container.current, value, candles.current);
    value.subscribeClick(param => {
      if (!handlers.current.quick || param.paneIndex !== 0 || !param.point || !candles.current) return;
      const price = candles.current.coordinateToPrice(param.point.y);
      if (price !== null && price > 0) handlers.current.onPrice(price.toFixed(nativePrecision.current));
    });
    const save = () => {
      const active = activeView.current;
      if (!active || !value.timeScale().getVisibleLogicalRange()) return;
      const scale = value.priceScale("right");
      const view = {barSpacing: value.timeScale().options().barSpacing, rightOffset: value.timeScale().scrollPosition(),
        priceRange: scale.options().autoScale ? null : scale.getVisibleRange()};
      try {localStorage.setItem(chartViewPrefix + active.identity, JSON.stringify(view));} catch { /* Chart remains usable without storage. */ }
    };
    saveView.current = save;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = () => {marketOverlays.current?.schedule();clearTimeout(timer); timer = setTimeout(save, 150);};
    value.timeScale().subscribeVisibleLogicalRangeChange(schedule);
    const element = container.current;
    element.addEventListener("pointerup", schedule);
    element.addEventListener("wheel", schedule, {passive: true});
    return () => {
      clearTimeout(timer); save(); activeView.current = null; saveView.current = () => {};
      value.timeScale().unsubscribeVisibleLogicalRangeChange(schedule);
      element.removeEventListener("pointerup", schedule); element.removeEventListener("wheel", schedule);
      marketOverlays.current?.remove();marketOverlays.current=null;
      value.remove(); chart.current = null; candles.current = null;signalMarkers.current=null;
    };
  }, []);
  useEffect(() => {
    const values = rows ?? [];
    const previous = previousCandles.current;
    if (activeView.current && (activeView.current.identity !== identity || !values.length)) {
      saveView.current(); activeView.current = null; fitted.current = "";
    }
    const incremental = previous.identity === identity && previous.rows.length > 0 &&
      (values.length === previous.rows.length || values.length === previous.rows.length + 1) &&
      previous.rows.slice(0, -1).every((row, i) => row === values[i]) &&
      values.at(-1)!.time >= previous.rows.at(-1)!.time;
    const point = (r: Market["candles"][number]) => ({time: r.time as UTCTimestamp, open: Number(r.open), high: Number(r.high), low: Number(r.low), close: Number(r.close)});
    if (incremental) {
      for (const row of values.slice(previous.rows.length - 1)) candles.current?.update(point(row));
    } else candles.current?.setData(values.map(point));
    previousCandles.current = {identity, rows: values};
    if (values.length && fitted.current !== identity) {
      const precision = Math.min(12, Math.max(...values.map(r => r.close.split(".")[1]?.length ?? 0)));
      nativePrecision.current = precision;
      let saved = null;
      try {saved = restoreChartView(localStorage.getItem(chartViewPrefix + identity));} catch { /* First view fits available data. */ }
      const value = chart.current;
      if (value) {
        value.priceScale("right").setAutoScale(true);
        if (saved) {
          value.timeScale().applyOptions({barSpacing: saved.barSpacing, rightOffset: Math.max(1 - values.length, saved.rightOffset)});
          if (saved.priceRange) {value.priceScale("right").setAutoScale(false); value.priceScale("right").setVisibleRange(saved.priceRange);}
        } else value.timeScale().fitContent();
      }
      activeView.current = {identity}; fitted.current = identity;
    }
  }, [rows, identity]);
  useEffect(() => {
    const value = chart.current; if (!value || !studies) return;
    const definitions = indicatorPlots([], activeStudies);
    const current = new Map<string, ISeriesApi<"Line"> | ISeriesApi<"Histogram">>();
    for (const plot of definitions) {
      const shared = {title: "", priceLineVisible: false, lastValueVisible: true};
      const series = plot.kind === "line"
        ? value.addSeries(LineSeries, {...shared, color: plot.color, lineWidth: plot.width, lineStyle: plot.style, crosshairMarkerVisible: false}, plot.pane)
        : value.addSeries(HistogramSeries, {...shared, color: plot.color, priceFormat: {type: plot.volume ? "volume" : "price"}}, plot.pane);
      current.set(plot.key, series);
    }
    plots.current = current;
    return () => {if(chart.current===value)current.forEach(series=>value.removeSeries(series));};
  }, [activeStudies, studies]);
  useEffect(() => {
    const value=chart.current;
    if(!value)return;
    const paneOwners=new Map<number,string>();
    for(const plot of indicatorPlots([],activeStudies)){if(plot.pane>0){const owner=activeStudies.find(s=>plot.key.startsWith(s.id+":"));if(owner)paneOwners.set(plot.pane,owner.id);}}
    value.panes().forEach((pane,i)=>pane.setStretchFactor(i===0?5:workspace.paneWeights[paneOwners.get(i)??""]??2));
  },[activeStudies,workspace.paneWeights]);
  useEffect(() => {
    const priceFormat = {type: "price" as const, precision: displayDigits, minMove: 10 ** -displayDigits};
    candles.current?.applyOptions({priceFormat});
    for (const plot of indicatorPlots([], activeStudies)) {
      if (plot.pane === 0) plots.current.get(plot.key)?.applyOptions({priceFormat});
    }
  }, [displayDigits, activeStudies]);
  useEffect(() => {
    if (!studies) return;
    const values = rows ?? [];
    for (const plot of indicatorPlots(values,activeStudies)) {
      const series = plots.current.get(plot.key);
      const data = plot.values.flatMap((v,i)=>v===null?[]:[{time:values[i].time as UTCTimestamp,value:v,...(plot.barColors?{color:plot.barColors[i]}:{})}]);
      series?.setData(data);
    }
  }, [rows, activeStudies, studies]);
  useEffect(()=>{
    const times=new Set((rows??[]).map(row=>row.time));
    const markers:SeriesMarker<Time>[]=[];
    for(const study of activeStudies){if(study.kind!=="SIGNAL")continue;
      for(const signal of crossoverSignals(rows??[],study.parameters[0],study.parameters[1])){
        if(!times.has(signal.time))continue;
        markers.push({time:signal.time as UTCTimestamp,position:signal.direction==="buy"?"belowBar":"aboveBar",shape:signal.direction==="buy"?"arrowUp":"arrowDown",color:study.colors[signal.direction==="buy"?0:1],text:signal.label});
      }
    }
    for(const signal of sanitizeChartSignals(strategySignals).filter(s=>s.source==="strategy"&&times.has(s.time))){
      markers.push({time:signal.time as UTCTimestamp,position:signal.direction==="buy"?"belowBar":"aboveBar",shape:signal.direction==="buy"?"arrowUp":"arrowDown",color:signal.direction==="buy"?"#2ebd85":"#f6465d",text:"策略·"+signal.label});
    }
    markers.sort((a,b)=>Number(a.time)-Number(b.time));
    signalMarkers.current?.setMarkers(markers.slice(-500));
  },[rows,activeStudies,strategySignals]);
  useEffect(() => {
    if(depthIdentity.current!==identity){depthHistory.current=[];depthIdentity.current=identity;}
    // The browser already receives Binance 20-level depth for the book panel.
    // Capture a bounded set of genuine snapshots, never invent historical depth.
    depthHistory.current=appendDepthSnapshot(depthHistory.current,depth);
    marketOverlays.current?.update({
      rows:rows??[],studies:activeStudies,depths:depthHistory.current,
      liquidations:modeledLiquidations,symbol:market?.symbol??"",now:Date.now(),
    });
  },[identity,rows,activeStudies,depth,modeledLiquidations,market?.symbol]);
  useEffect(() => {
    const series = candles.current; if (!series) return;
    const lines: ReturnType<typeof series.createPriceLine>[] = [];
    function add(price: string | null | undefined, color: string, dashed = true) {
      const number = Number(price); if (!Number.isFinite(number) || number <= 0) return;
      lines.push(series!.createPriceLine({price:number,title:"",color,lineWidth:1,lineStyle:dashed?2:0,axisLabelVisible:true}));
    }
    for (const p of state?.positions ?? []) {
      const color=p.side==="long"?"#2ebd85":"#f6465d";
      if (options.positions) add(p.entry_price,color,false);
      if (options.breakeven) add(p.break_even_price,"#9063b5");
      if (options.liquidation) add(p.liquidation_price,"#c96b20");
    }
    for (const o of state?.orders.slice(0,50) ?? []) {
      if (o.open && options.orders || !o.open && options.history) add(o.price,o.open?"#3978bc":"#848e9c");
    }
    if (options.preview) add(preview,"#b8901c");
    if (options.alerts && armed !== null) add(String(armed),"#d94ab1");
    return () => {lines.forEach(l=>series.removePriceLine(l));};
  }, [state, options, preview, armed]);
  useEffect(() => {
    const current = Number(market?.last_price);
    if (!current || !options.alerts || armed === null) {previous.current=null; return;}
    const before=previous.current; previous.current=current;
    if (before !== null && (before-armed)*(current-armed)<=0) {
      onNotice(`价格提醒：${market!.symbol} 已触及 ${armed}（仅当前页面运行时提醒）`);
      // Disarm after crossing without changing a persisted display preference.
      const timer=window.setTimeout(()=>setArmed(null),0); return ()=>window.clearTimeout(timer);
    }
  }, [market, options.alerts, armed, onNotice]);
  useEffect(() => {previous.current=null; const timer=window.setTimeout(()=>{setArmed(null);setAlertPrice("");},0);return()=>window.clearTimeout(timer);},[identity]);
  return <><div className="trade-chart-toolbar trade-chart-tools"><div className="trade-chart-primary">{periods}<div className="trade-chart-menu-anchor"><button aria-label="技术指标" title="技术指标" disabled={studies === null} aria-expanded={menu==="indicators"} onClick={()=>setMenu(menu==="indicators"?null:"indicators")}><span aria-hidden="true">ƒx</span></button><button aria-label="更多设置" title="更多设置" aria-expanded={menu==="display"} onClick={()=>setMenu(menu==="display"?null:"display")}><span aria-hidden="true">⚙</span></button>
    {menu==="display" && <div className="trade-chart-menu"><div className="trade-chart-menu-title">图表显示<button aria-label="关闭图表菜单" onClick={()=>setMenu(null)}>×</button></div>{Object.entries(displays).map(([key,label])=><label key={key}><input type="checkbox" checked={options[key as keyof Options]} onChange={()=>toggle(key as keyof Options)}/>{label}</label>)}<p>账户价格线仅显示有效快照。损益两平与强平价以交易所返回为准；缺失时不显示。</p>{options.alerts && <div className="trade-alert-input"><input aria-label="提醒价格" inputMode="decimal" placeholder="提醒价格" value={alertPrice} onChange={e=>setAlertPrice(e.target.value)}/><button disabled={!Number.isFinite(Number(alertPrice))||Number(alertPrice)<=0||!market} onClick={()=>{previous.current=Number(market!.last_price);setArmed(Number(alertPrice));}}>设定</button><button onClick={()=>setArmed(null)}>清除</button><p>{armed===null?"未设置提醒":`提醒价 ${armed}`} · 仅当前页面打开时有效</p></div>}</div>}
    </div></div>{toolbar}</div>
    <div className="trade-chart-shell">
      <div className="trade-chart" ref={container} aria-label="永续合约 K 线、技术指标与信号" />
      {studies && studies.length>0 && <div className="trade-study-legends" aria-label="已添加的图表指标">
        {studies.map((study,index)=><div className={"trade-study-legend"+(!studyVisibleForInterval(study,interval)?" inactive":"")} key={study.id}>
          <span className="trade-study-legend-color" style={{background:study.colors[0]}} />
          <span className="trade-study-legend-name" title={study.kind==="CUSTOM"?study.formula:study.kind}>{study.kind}{study.parameters.length?" ("+study.parameters.join(", ")+")":""}</span>
          <small>{["RSI","MACD","VOL"].includes(study.kind)||study.kind==="CUSTOM"&&study.pane==="separate"?"副图":"主图"}</small>
          <span className="trade-study-legend-actions">
            <button type="button" aria-label={study.visible?"隐藏 "+study.kind:"显示 "+study.kind} title={study.visible?"隐藏":"显示"} onClick={()=>toggleStudy(study.id)}>{study.visible?"◉":"○"}</button>
            <button type="button" aria-label={"设置 "+study.kind} title="设置参数和样式" onClick={()=>setEditingStudyId(study.id)}>⚙</button>
            <button type="button" aria-label={"上移 "+study.kind} title="上移" disabled={index===0} onClick={()=>moveStudy(index,-1)}>↑</button>
            <button type="button" aria-label={"下移 "+study.kind} title="下移" disabled={index===studies.length-1} onClick={()=>moveStudy(index,1)}>↓</button>
            <button type="button" aria-label={"删除 "+study.kind} title="删除" onClick={()=>removeStudy(study.id)}>×</button>
          </span>
        </div>)}
      </div>}
    </div>
    {menu==="indicators" && studies && <IndicatorLibrary configs={studies} onChange={updateStudies} workspace={workspace} onWorkspaceChange={updateWorkspace} onEdit={id=>{setMenu(null);setEditingStudyId(id);}} onClose={()=>setMenu(null)} />}
    {editingStudy && <IndicatorEditor key={editingStudy.id} config={editingStudy} weight={workspace.paneWeights[editingStudy.id]??2} onApply={applyStudy} onClose={()=>setEditingStudyId(null)} />}
  </>;
}
