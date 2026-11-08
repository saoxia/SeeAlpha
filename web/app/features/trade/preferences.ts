export const tradeIntervals = ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"];
export const selectionKey = "seealpha.trade.selection";
export const chartViewPrefix = "seealpha.trade.chart-view:";

export function restoreTradeSelection(saved: string | null): {symbol: string; interval: string} {
  const defaults = {symbol: "BTCUSDT-PERP", interval: "15m"};
  try {
    const value = JSON.parse(saved ?? "null");
    return {
      symbol: typeof value?.symbol === "string" && /^[A-Z0-9]{2,30}(?:USDT|USDC)-PERP$/.test(value.symbol) ? value.symbol : defaults.symbol,
      interval: tradeIntervals.includes(value?.interval) ? value.interval : defaults.interval,
    };
  } catch {return defaults;}
}

export type ChartView = {barSpacing: number; rightOffset: number; priceRange: {from: number; to: number} | null};
export function restoreChartView(saved: string | null): ChartView | null {
  try {
    const value = JSON.parse(saved ?? "null");
    if (!value || typeof value.barSpacing !== "number" || !Number.isFinite(value.barSpacing) || value.barSpacing <= 0 ||
        typeof value.rightOffset !== "number" || !Number.isFinite(value.rightOffset)) return null;
    const range = value.priceRange;
    return {barSpacing: value.barSpacing, rightOffset: value.rightOffset,
      priceRange: range && typeof range.from === "number" && typeof range.to === "number" && Number.isFinite(range.from) && Number.isFinite(range.to) && range.from > 0 && range.to > range.from ? {from: range.from, to: range.to} : null};
  } catch {return null;}
}
