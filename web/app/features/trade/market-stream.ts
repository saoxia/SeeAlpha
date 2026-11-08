import type { Market } from "./api.ts";

type Candle = Market["candles"][number];
type WireEvent = {
  e: string; E: number; s: string;
  k?: {t: number; i: string; o: string; h: string; l: string; c: string; v: string};
  b?: string[][]; a?: string[][] | number;
  c?: string; P?: string; h?: string; l?: string; q?: string;
  p?: string; i?: string; r?: string; T?: number;
  t?: number; v?: string; m?: boolean;
};

export function mergeCandle(rows: Candle[], candle: Candle): Candle[] {
  const values = new Map(rows.map(row => [row.time, row]));
  values.set(candle.time, candle);
  return [...values.values()].sort((a, b) => a.time - b.time).slice(-300);
}

export function marketUrls(environment: string, symbol: string, interval: string) {
  const raw = symbol.replace(/-PERP$/, "");
  const stream = raw.toLowerCase();
  const host = environment === "sandbox" ? "demo-fstream.binance.com" : "fstream.binance.com";
  return {
    rest: environment === "sandbox" ? "https://demo-fapi.binance.com/fapi/v1/" : "https://fapi.binance.com/fapi/v1/",
    market: `wss://${host}/market/stream?streams=${stream}@kline_${interval}/${stream}@aggTrade/${stream}@ticker/${stream}@markPrice@1s`,
    book: `wss://${host}/public/stream?streams=${stream}@depth20@100ms`,
    raw,
  };
}

export function applyMarketEvent(market: Market, event: WireEvent): Market {
  if (event.s !== market.symbol.replace(/-PERP$/, "") || !Number.isFinite(event.E)) return market;
  if (event.e === "kline" && event.k?.i === market.interval) {
    const k = event.k;
    if (![k.o, k.h, k.l, k.c, k.v].every(v => typeof v === "string" && Number.isFinite(Number(v)))) return market;
    return {...market, checked_at: event.E, candles: mergeCandle(market.candles, {time: k.t / 1000, open: k.o, high: k.h, low: k.l, close: k.c, volume: k.v})};
  }
  if (event.e === "depthUpdate" && Array.isArray(event.b) && Array.isArray(event.a)) return {...market, bids: event.b, asks: event.a, depth_time: event.E};
  if (event.e === "24hrTicker" && event.c && event.P && event.h && event.l && event.q) return {...market, last_price: event.c, change_percent: event.P, change_price: event.p, base_volume: event.v, high: event.h, low: event.l, quote_volume: event.q};
  if (event.e === "markPriceUpdate" && event.p && event.i && event.r && event.T) return {...market, mark_price: event.p, index_price: event.i, funding_rate: event.r, next_funding_time: event.T};
  if (event.e === "aggTrade" && typeof event.a === "number" && event.p && event.q !== undefined) {
    // Aggregate trades are display prints; they never enter the raw-trade store.
    const trade = {id: Number(event.a), price: event.p, qty: event.q, time: event.T!, isBuyerMaker: Boolean(event.m)};
    return {...market, last_price: event.p, trades: [...market.trades.filter(t => t.id !== trade.id), trade].slice(-30)};
  }
  return market;
}

/** Public browser-only display feed. No credentials or order commands. */
export function subscribeMarket(symbol: string, environment: "production" | "sandbox", interval: string,
  onMarket: (market: Market) => void, onError: (message: string) => void) {
  const urls = marketUrls(environment, symbol, interval);
  let stopped = false, retry = 1000;
  let sockets: WebSocket[] = [];
  let controller: AbortController | undefined;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;
  let flushTimer: ReturnType<typeof setTimeout> | undefined;
  let interestTimer: ReturnType<typeof setTimeout> | undefined;
  let watchdog: ReturnType<typeof setInterval> | undefined;
  let generation = 0;
  async function connect() {
    const current = ++generation;
    controller = new AbortController();
    const signal = controller.signal;
    let value: Market | undefined;
    const last = [Date.now(), Date.now()];
    const buffer: WireEvent[] = [];
    let failed = false;
    function fail() {
      if (stopped || failed || current !== generation) return;
      failed = true;
      controller?.abort();
      sockets.forEach(socket => socket.close());
      clearTimeout(interestTimer); clearInterval(watchdog); clearTimeout(flushTimer); flushTimer = undefined;
      onError("Binance 直连中断，正在重连；请检查浏览器网络。");
      retryTimer = setTimeout(() => void connect(), retry);
      retry = Math.min(retry * 2, 30000);
    }
    try {
      sockets = [urls.market, urls.book].map((url, index) => {
        const socket = new WebSocket(url);
        socket.onmessage = message => {
          if (stopped || failed || current !== generation) return;
          try {
            const event = JSON.parse(message.data).data as WireEvent;
            if (!event || event.s !== urls.raw || !Number.isFinite(event.E)) return;
            last[index] = Date.now();
            if (!value) {
              if (buffer.length >= 5000) {fail(); return;}
              buffer.push(event); return;
            }
            value = applyMarketEvent(value, event);
            if (!flushTimer) flushTimer = setTimeout(() => {
              flushTimer = undefined;
              if (!stopped && !failed && value) {onMarket(value); onError(""); retry = 1000;}
            }, 100);
          } catch {fail();}
        };
        socket.onerror = fail; socket.onclose = fail;
        return socket;
      });
      watchdog = setInterval(() => {if (last.some(time => Date.now() - time > 30000)) fail();}, 5000);
      const get = async <T>(endpoint: string, params: Record<string, string> = {}): Promise<T> => {
        const response = await fetch(`${urls.rest}${endpoint}?${new URLSearchParams(endpoint === "fundingInfo" ? {} : {symbol: urls.raw, ...params})}`, {
          credentials: "omit", signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]), cache: "no-store",
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      };
      const [candles, ticker, premium] = await Promise.all([
        get<(string | number)[][]>("klines", {interval, limit: "300"}),
        get<{lastPrice: string; priceChangePercent: string; highPrice: string; lowPrice: string; quoteVolume: string; volume: string; priceChange: string}>("ticker/24hr"),
        get<{markPrice: string; indexPrice: string; lastFundingRate: string; nextFundingTime: number}>("premiumIndex"),
      ]);
      if (stopped || failed || current !== generation) return;
      value = {symbol, environment, interval, checked_at: 0,
        candles: candles.map(r => ({time: Number(r[0]) / 1000, open: String(r[1]), high: String(r[2]), low: String(r[3]), close: String(r[4]), volume: String(r[5])})),
        bids: [], asks: [], trades: [], depth_time: null, last_price: ticker.lastPrice,
        change_percent: ticker.priceChangePercent, change_price: ticker.priceChange, base_volume: ticker.volume, high: ticker.highPrice, low: ticker.lowPrice, quote_volume: ticker.quoteVolume,
        mark_price: premium.markPrice, index_price: premium.indexPrice, funding_rate: premium.lastFundingRate, next_funding_time: premium.nextFundingTime};
      for (const event of buffer) value = applyMarketEvent(value, event);
      onMarket(value);
      onError(value.checked_at ? "" : "正在等待 Binance 实时 K 线…");
      async function loadInterest() {
        const [interest, funding] = await Promise.allSettled([
          get<{symbol: string; openInterest: string; time: number}>("openInterest"),
          get<{symbol: string; fundingIntervalHours: number}[]>("fundingInfo"),
        ]);
        if (stopped || failed || current !== generation || !value) return;
        if (interest.status === "fulfilled" && interest.value.symbol === urls.raw && typeof interest.value.openInterest === "string" && /^\d+(?:\.\d+)?$/.test(interest.value.openInterest) &&
            Number.isFinite(Number(interest.value.openInterest)) && Number(interest.value.openInterest) >= 0 && Number.isFinite(interest.value.time)) {
          value = {...value, open_interest: interest.value.openInterest, open_interest_checked_at: interest.value.time};
        }
        if (funding.status === "fulfilled" && Array.isArray(funding.value)) {
          const hours = funding.value.find(row=>row.symbol===urls.raw)?.fundingIntervalHours ?? 8;
          if (Number.isInteger(hours) && hours > 0) value = {...value, funding_interval_hours: hours};
        }
        onMarket(value);
        interestTimer = setTimeout(loadInterest, 30000);
      }
      void loadInterest();
    } catch {fail();}
  }
  void connect();
  return () => {
    stopped = true; generation++;
    controller?.abort(); clearTimeout(interestTimer); clearTimeout(retryTimer); clearTimeout(flushTimer); clearInterval(watchdog);
    sockets.forEach(socket => socket.close());
  };
}
