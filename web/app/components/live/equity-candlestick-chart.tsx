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
import { requestEquityRebuild } from "@/app/features/live/equity";
import { subscribeTheme } from "@/app/features/appearance/theme";

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
  return `${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`;
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
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [rebuild, setRebuild] = useState<{ accountId: number; id: string; status: string; message: string } | null>(null);
  const rebuildController = useRef<AbortController | null>(null);

  useEffect(() => () => rebuildController.current?.abort(), [accountId]);

  async function startRebuild() {
    if (accountId === null) return;
    rebuildController.current?.abort();
    const controller = new AbortController();
    rebuildController.current = controller;
    setRebuild({ accountId, id: "", status: "queued", message: "正在提交重算…" });
    try {
      const job = await requestEquityRebuild(accountId, controller.signal);
      if (!controller.signal.aborted) setRebuild({ accountId, id: job.id, status: job.status, message: "正在重算已保存的资产证据…" });
    } catch (requestError) {
      setRebuild((current) => current?.accountId !== accountId ? current : {
        accountId, id: "", status: controller.signal.aborted ? "unknown" : "failed",
        message: controller.signal.aborted ? "已停止进度查询，后台任务可能仍在运行。" : requestError instanceof Error ? requestError.message : "重算失败。",
      });
    }
  }

  const rebuilding = rebuild?.accountId === accountId && ["queued", "running"].includes(rebuild.status);
  const rebuildJobId = rebuilding ? rebuild?.id : undefined;
  useEffect(() => {
    if (!rebuildJobId || accountId === null) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const job = await requestEquityRebuild(accountId!, controller.signal, rebuildJobId);
        if (controller.signal.aborted) return;
        setRebuild({ accountId: accountId!, id: job.id, status: job.status, message: job.message || "正在重算已保存的资产证据…" });
        if (["queued", "running"].includes(job.status)) timer = setTimeout(() => void poll(), 1000);
        else if (job.status === "success") setRefreshVersion((version) => version + 1);
      } catch (requestError) {
        if (!controller.signal.aborted) setRebuild({ accountId: accountId!, id: rebuildJobId!, status: "unknown", message: `${requestError instanceof Error ? requestError.message : "无法读取重算进度。"} 后台任务可能仍在运行。` });
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [accountId, rebuildJobId]);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        fontSize: 10,
        attributionLogo: true,
      },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { scaleMargins: { top: 0.18, bottom: 0.08 } },
      timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 3, barSpacing: 8, minBarSpacing: 2 },
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
    function applyAppearance() {
      if (!containerRef.current) return;
      const style = getComputedStyle(containerRef.current);
      chart.applyOptions({
        layout: {
          background: { type: ColorType.Solid, color: style.getPropertyValue("--chart-background").trim() },
          textColor: style.getPropertyValue("--chart-text").trim(),
          fontFamily: style.getPropertyValue("--chart-font").trim(),
        },
        rightPriceScale: { borderColor: style.getPropertyValue("--chart-border").trim() },
        timeScale: { borderColor: style.getPropertyValue("--chart-border").trim() },
      });
    }
    applyAppearance();
    const unsubscribe = subscribeTheme(applyAppearance);
    return () => {
      unsubscribe();
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
  }, [accountId, interval, refreshVersion]);

  const intervalSeconds = intervals.find((item) => item.value === interval)?.seconds ?? 300;
  const activeSeries = series?.account_id === accountId && series.interval_seconds === intervalSeconds ? series : null;
  const candles = useMemo(() => numericCandles(activeSeries), [activeSeries]);
  const latest = candles.at(-1);
  const first = candles.at(0);
  const change = latest && first ? latest.close - first.open : 0;
  const changePercent = first?.open ? change / first.open * 100 : 0;
  const currency = activeSeries?.currency || "USDT";

  useEffect(() => {
    const chart = chartRef.current;
    const candleSeries = candleSeriesRef.current;
    if (!chart || !candleSeries) return;
    candleSeries.setData([...candles, ...(activeSeries?.gaps ?? []).map((stamp) => ({ time: Math.floor(stamp / 1000) as UTCTimestamp }))].sort((a, b) => Number(a.time) - Number(b.time)));
    if (!candles.length) return;
    const fitKey = `${accountId ?? "overview"}:${interval}`;
    if (fittedKeyRef.current !== fitKey) {
      chart.timeScale().fitContent();
      fittedKeyRef.current = fitKey;
    }
  }, [accountId, activeSeries, candles, interval]);

  return (
    <div className="equity-chart">
      <div className="equity-chart-head">
        <div className="equity-chart-title"><span>{accountName}</span><b>预估资产</b></div>
        <div className="equity-chart-toolbar" aria-label="预估资产 K 线周期">
          {intervals.map((item) => (
            <button key={item.value} type="button" className={interval === item.value ? "on" : ""} onClick={() => setInterval(item.value)}>
              {item.label}
            </button>
          ))}
          <button type="button" disabled={accountId === null || rebuilding} onClick={() => void startRebuild()} title={accountId === null ? "请先选择单个账户" : "按已保存的证据重算资产曲线"}>
            {rebuilding ? "正在重算…" : "重算资产曲线"}
          </button>
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
      {!loading && !error && !candles.length ? <p className="equity-chart-state">暂无完整资产 K 线，等待账户基准和有效价格。</p> : null}
      {activeSeries?.warnings?.length ? <p className="equity-chart-warning">{activeSeries.warnings.join(" ")}</p> : null}
      {rebuild?.accountId === accountId ? <p role="status" className="equity-chart-warning">{rebuild.message}</p> : null}
      {error && candles.length ? <span className="equity-chart-warning">刷新失败，正在显示最近缓存</span> : null}
    </div>
  );
}
