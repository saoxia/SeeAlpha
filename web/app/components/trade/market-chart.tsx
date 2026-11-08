"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { CandlestickSeries, ColorType, HistogramSeries, LineSeries, createChart, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import type { Market, TradeState } from "@/app/features/trade/api";
import { indicatorPlots, restoreIndicators, type IndicatorConfig } from "@/app/features/trade/indicators";
import { chartViewPrefix, restoreChartView } from "@/app/features/trade/preferences";
import { IndicatorSettings } from "./indicator-settings";

const defaults = {orders: true, positions: true, history: false, breakeven: false, liquidation: false, preview: true, alerts: false, quick: false};
type Options = typeof defaults;
const displays = {quick: "快捷填价（点击图表）", orders: "当前委托", positions: "持有仓位", history: "历史委托（委托价）", breakeven: "损益两平价", liquidation: "强平价格", alerts: "价格提醒", preview: "订单预览线"} as const;
export function MarketChart({ market, identity, state, preview, onPrice, onNotice, displayDigits, periods, toolbar }: {
  periods: ReactNode; toolbar: ReactNode; displayDigits: number; market: Pick<Market, "candles" | "last_price" | "symbol"> | null; identity: string; state: TradeState | null; preview: string; onPrice: (price: string) => void; onNotice: (message: string) => void;
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
  const plots = useRef(new Map<string, ISeriesApi<"Line"> | ISeriesApi<"Histogram">>());
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
    const schedule = () => {clearTimeout(timer); timer = setTimeout(save, 150);};
    value.timeScale().subscribeVisibleLogicalRangeChange(schedule);
    const element = container.current;
    element.addEventListener("pointerup", schedule);
    element.addEventListener("wheel", schedule, {passive: true});
    return () => {
      clearTimeout(timer); save(); activeView.current = null; saveView.current = () => {};
      value.timeScale().unsubscribeVisibleLogicalRangeChange(schedule);
      element.removeEventListener("pointerup", schedule); element.removeEventListener("wheel", schedule);
      value.remove(); chart.current = null; candles.current = null;
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
    const definitions = indicatorPlots([], studies);
    const current = new Map<string, ISeriesApi<"Line"> | ISeriesApi<"Histogram">>();
    for (const plot of definitions) {
      const shared = {title: "", priceLineVisible: false, lastValueVisible: true};
      const series = plot.kind === "line"
        ? value.addSeries(LineSeries, {...shared, color: plot.color, lineWidth: plot.width, lineStyle: plot.style, crosshairMarkerVisible: false}, plot.pane)
        : value.addSeries(HistogramSeries, {...shared, color: plot.color, priceFormat: {type: plot.volume ? "volume" : "price"}}, plot.pane);
      current.set(plot.key, series);
    }
    plots.current = current;
    value.panes().forEach((pane,i)=>pane.setStretchFactor(i===0?5:1));
    return () => {if(chart.current===value)current.forEach(series=>value.removeSeries(series));};
  }, [studies]);
  useEffect(() => {
    const priceFormat = {type: "price" as const, precision: displayDigits, minMove: 10 ** -displayDigits};
    candles.current?.applyOptions({priceFormat});
    for (const plot of indicatorPlots([], studies ?? [])) {
      if (plot.pane === 0) plots.current.get(plot.key)?.applyOptions({priceFormat});
    }
  }, [displayDigits, studies]);
  useEffect(() => {
    if (!studies) return;
    const values = rows ?? [];
    for (const plot of indicatorPlots(values,studies)) {
      const series = plots.current.get(plot.key);
      const data = plot.values.flatMap((v,i)=>v===null?[]:[{time:values[i].time as UTCTimestamp,value:v,...(plot.barColors?{color:plot.barColors[i]}:{})}]);
      series?.setData(data);
    }
  }, [rows, studies]);
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
    {menu==="indicators" && studies && <IndicatorSettings configs={studies} onChange={updateStudies} onClose={()=>setMenu(null)} candleCount={market?.candles.length??0} />}
    {menu==="display" && <div className="trade-chart-menu"><div className="trade-chart-menu-title">图表显示<button aria-label="关闭图表菜单" onClick={()=>setMenu(null)}>×</button></div>{Object.entries(displays).map(([key,label])=><label key={key}><input type="checkbox" checked={options[key as keyof Options]} onChange={()=>toggle(key as keyof Options)}/>{label}</label>)}<p>账户价格线仅显示有效快照。损益两平与强平价以交易所返回为准；缺失时不显示。</p>{options.alerts && <div className="trade-alert-input"><input aria-label="提醒价格" inputMode="decimal" placeholder="提醒价格" value={alertPrice} onChange={e=>setAlertPrice(e.target.value)}/><button disabled={!Number.isFinite(Number(alertPrice))||Number(alertPrice)<=0||!market} onClick={()=>{previous.current=Number(market!.last_price);setArmed(Number(alertPrice));}}>设定</button><button onClick={()=>setArmed(null)}>清除</button><p>{armed===null?"未设置提醒":`提醒价 ${armed}`} · 仅当前页面打开时有效</p></div>}</div>}
    </div></div>{toolbar}</div><div className="trade-chart" ref={container} aria-label="TradingView 永续合约 K 线及技术指标" /></>;
}
