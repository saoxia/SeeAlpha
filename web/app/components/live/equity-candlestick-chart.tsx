"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { EquityCandleSeries } from "../types";

type Props = { accountId: number | null; accountName: string };
type Interval = "5m" | "15m" | "1h" | "4h" | "1d";
type NumericCandle = { time: UTCTimestamp; open: number; high: number; low: number; close: number };

const intervals: { value: Interval; label: string; seconds: number }[] = [
  { value: "5m", label: "5分", seconds: 300 },
  { value: "15m", label: "15分", seconds: 900 },
  { value: "1h", label: "1小时", seconds: 3600 },
  { value: "4h", label: "4小时", seconds: 14_400 },
  { value: "1d", label: "日线", seconds: 86_400 },
];

function money(value: number, currency: string) {
  return value.toLocaleString("en-US", { style: "currency", currency, minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function compactNumber(value: number) {
  return value.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

function numericCandles(series: EquityCandleSeries | null): NumericCandle[] {
  if (!series) return [];
  return series.candles.flatMap((candle) => {
    const values = [candle.open, candle.high, candle.low, candle.close].map(Number);
    if (values.some((value) => !Number.isFinite(value))) return [];
    return [{ time: Math.floor(candle.bucket_start / 1000) as UTCTimestamp, open: values[0], high: values[1], low: values[2], close: values[3] }];
  });
}

export function EquityCandlestickChart({ accountId, accountName }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const fittedKeyRef = useRef("");
  const [interval, setInterval] = useState<Interval>("5m");
  const [series, setSeries] = useState<EquityCandleSeries | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#f8f8f6" },
        textColor: "#777a80",
        fontFamily: '"Courier New", monospace',
        fontSize: 10,
        attributionLogo: true,
      },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#c9cbce", scaleMargins: { top: 0.18, bottom: 0.08 } },
      timeScale: { borderColor: "#c9cbce", timeVisible: true, secondsVisible: false, rightOffset: 3, barSpacing: 8, minBarSpacing: 2 },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: {
        axisPressedMouseMove: { time: true, price: true },
        axisDoubleClickReset: { time: true, price: true },
        mouseWheel: true,
        pinch: true,
      },
    });
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#16845b",
      downColor: "#d64d4d",
      borderVisible: false,
      wickUpColor: "#16845b",
      wickDownColor: "#d64d4d",
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });
    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    return () => {
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    let active = true;
    let controller: AbortController | null = null;
    fittedKeyRef.current = "";
    const load = async (background = false) => {
      controller?.abort();
      controller = new AbortController();
      if (!background) setLoading(true);
      try {
        const query = `limit=500&interval=${interval}`;
        const path = accountId === null
          ? `/api/exchange-accounts/equity-candles?${query}`
          : `/api/exchange-accounts/${accountId}/equity-candles?${query}`;
        const response = await fetch(path, { cache: "no-store", credentials: "same-origin", signal: controller.signal });
        if (!response.ok) throw new Error("预估资产 K 线读取失败");
        const payload = await response.json() as EquityCandleSeries;
        if (payload.metric !== "total_equity") throw new Error("K 线指标不是预估资产");
        if (active) { setSeries(payload); setError(""); }
      } catch (requestError) {
        if (requestError instanceof DOMException && requestError.name === "AbortError") return;
        if (active) setError(requestError instanceof Error ? requestError.message : "预估资产 K 线读取失败");
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    const timer = window.setInterval(() => void load(true), 60_000);
    return () => { active = false; controller?.abort(); window.clearInterval(timer); };
  }, [accountId, interval]);

  const intervalSeconds = intervals.find((item) => item.value === interval)?.seconds ?? 300;
  const activeSeries = series?.account_id === accountId && series.interval_seconds === intervalSeconds ? series : null;
  const candles = useMemo(() => numericCandles(activeSeries), [activeSeries]);
  const latest = candles.at(-1);
  const first = candles.at(0);
  const change = latest && first ? latest.close - first.open : 0;
  const changePercent = first?.open ? change / first.open * 100 : 0;
  const currency = activeSeries?.currency || "USD";

  useEffect(() => {
    const chart = chartRef.current;
    const candleSeries = candleSeriesRef.current;
    if (!chart || !candleSeries) return;
    candleSeries.setData(candles);
    if (!candles.length) return;
    const fitKey = `${accountId ?? "overview"}:${interval}`;
    if (fittedKeyRef.current !== fitKey) {
      chart.timeScale().fitContent();
      fittedKeyRef.current = fitKey;
    }
  }, [accountId, candles, interval]);

  return (
    <div className="equity-chart">
      <div className="equity-chart-head">
        <div className="equity-chart-title"><span>{accountName}</span><b>预估资产 {activeSeries?.is_sample ? "（示例数据）" : ""}</b></div>
        <div className="equity-chart-toolbar" aria-label="预估资产 K 线周期">
          {intervals.map((item) => (
            <button key={item.value} type="button" className={interval === item.value ? "on" : ""} onClick={() => setInterval(item.value)}>
              {item.label}
            </button>
          ))}
        </div>
        {latest ? (
          <div className="equity-chart-price">
            <strong>{money(latest.close, currency)}</strong>
            <em className={change >= 0 ? "positive" : "negative"}>{change >= 0 ? "+" : ""}{compactNumber(change)} · {changePercent >= 0 ? "+" : ""}{changePercent.toFixed(2)}%</em>
          </div>
        ) : null}
      </div>
      <div ref={containerRef} className="equity-chart-canvas" aria-label={`${accountName} 预估资产 K 线`} />
      {loading && !candles.length ? <p className="equity-chart-state">正在读取预估资产…</p> : null}
      {!loading && error && !candles.length ? <p className="equity-chart-state error">{error}</p> : null}
      {!loading && !error && !candles.length ? <p className="equity-chart-state">暂无预估资产 K 线。</p> : null}
      {error && candles.length ? <span className="equity-chart-warning">刷新失败，正在显示最近缓存</span> : null}
    </div>
  );
}
