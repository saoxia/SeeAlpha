"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { isFresh, orderBlockReason, orderError, requestId, tradingRequest, parseBatchLimitLines, matchesIncrement, executeManualBatch, type BatchTask, type TradingAccount, type Market, type Order, type OrderRequest, type Result, type TradeState } from "@/app/features/trade/api";
import { formatPrice, priceDigits, fundingCountdown } from "@/app/features/trade/display";
import { subscribeMarket } from "@/app/features/trade/market-stream";
import type { components } from "@/app/api/generated/schema";
import type { ExchangeTrade } from "../types";
import type { LiquidationEstimates } from "@/app/features/trade/market-overlays";
import type { ChartSignal } from "@/app/features/trade/chart-signals";
import { dataManagementApi } from "@/app/features/data-management/api";
import { MarketChart } from "./market-chart";
import { MarketPicker } from "./market-picker";
import { tradeIntervals as intervals, selectionKey, restoreTradeSelection } from "@/app/features/trade/preferences";
const pendingKey = "seealpha.manual.pending";
type Pending = {accountId: number; requestId: string; symbol: string; environment: string};
type BatchPlan = { accountId: number; kind: "submit" | "cancel"; tasks: BatchTask[]; lines: string[]; };
type AlgoOrder = components["schemas"]["ManualAlgoOrderView"];
type AlgoRequest = components["schemas"]["AlgoOrderValues"];
type LeverageRequest = components["schemas"]["LeverageChangeValues"];
type AlgoCancelRequest = components["schemas"]["AlgoCancelValues"];
type ProfessionalPlan = {
  accountId: number; action: "algo_submit" | "algo_cancel" | "leverage";
  payload: AlgoRequest | LeverageRequest | AlgoCancelRequest; title: string; details: string[];
};
type DevTenant = {id:number; name:string; purpose:string};
type VirtualAccount = {id:number; account_name:string; kind:string};
function number(value: string | undefined, digits = 6) {return value === undefined ? "—" : Number(value).toLocaleString("en-US", {maximumFractionDigits: digits});}
function clock(ms: number) {return new Date(ms).toLocaleTimeString("zh-CN", {hour12: false});}

export function TradePage() {
  const [accounts, setAccounts] = useState<TradingAccount[]>([]);
  const [accountId, setAccountId] = useState("");
  const [symbol, setSymbol] = useState("BTCUSDT-PERP");
  const product = symbol.endsWith("-PERP") ? "futures_um" : "spot";
  const [preferencesReady, setPreferencesReady] = useState(false);
  const [interval, setInterval] = useState("15m");
  const [displayDigits, setDisplayDigits] = useState<number | null>(null);
  const [fills, setFills] = useState<ExchangeTrade[]>([]);
  const [fillError, setFillError] = useState("");
  const [chartSource, setChartSource] = useState<"binance" | "local">("binance");
  const [localChart, setLocalChart] = useState<components["schemas"]["LocalChartResponse"] | null>(null);
  const [localError, setLocalError] = useState("");
  const [marketData, setMarket] = useState<Market | null>(null);
  const [state, setState] = useState<TradeState | null>(null);
  const [marketError, setMarketError] = useState("");
  const [accountError, setAccountError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const refreshing = useRef<string | null>(null);
  const [orderType, setOrderType] = useState<"limit" | "market">("limit");
  const [price, setPrice] = useState("");
  const [quantity, setQuantity] = useState("");
  const [expanded, setExpanded] = useState(false);
  const page = useRef<HTMLElement>(null);
  const [reduceOnly, setReduceOnly] = useState(false);
  const [postOnly, setPostOnly] = useState(false);
  const [tif, setTif] = useState<"GTC" | "IOC" | "FOK">("GTC");
  const [confirmation, setConfirmation] = useState<{accountId: number; order: OrderRequest} | null>(null);
  const [cancelOrder, setCancelOrder] = useState<Order | null>(null);
  const [batchDraft, setBatchDraft] = useState("");
  const [batchPlan, setBatchPlan] = useState<BatchPlan | null>(null);
  const [tradeMode, setTradeMode] = useState<"standard" | "conditional" | "leverage">("standard");
  const [professionalPlan, setProfessionalPlan] = useState<ProfessionalPlan | null>(null);
  const [algoType, setAlgoType] = useState<AlgoRequest["order_type"]>("STOP_MARKET");
  const [algoQty, setAlgoQty] = useState("");
  const [triggerPrice, setTriggerPrice] = useState("");
  const [stopLimitPrice, setStopLimitPrice] = useState("");
  const [activationPrice, setActivationPrice] = useState("");
  const [callbackRate, setCallbackRate] = useState("1");
  const [workingType, setWorkingType] = useState<"MARK_PRICE" | "CONTRACT_PRICE">("MARK_PRICE");
  const [priceProtect, setPriceProtect] = useState(false);
  const [newLeverage, setNewLeverage] = useState("");
  const [liquidationModel, setLiquidationModel] = useState<LiquidationEstimates | null>(null);
  const [strategyMarkers, setStrategyMarkers] = useState<ChartSignal[]>([]);
  const [strategySources, setStrategySources] = useState<Array<{value:string;label:string}>>([]);
  const [selectedStrategySource, setSelectedStrategySource] = useState("");
  const [strategyMessage, setStrategyMessage] = useState("");
  const [selectedCancels, setSelectedCancels] = useState<string[]>([]);
  const [pending, setPending] = useState<Pending | null>(null);
  const [table, setTable] = useState<"positions" | "orders" | "algo" | "history" | "trades">("positions");
  const [bookTab, setBookTab] = useState<"book" | "trades">("book");
  const [now, setNow] = useState(() => Date.now());
  const account = accounts.find(a => String(a.id) === accountId);
  const environment: "production" | "sandbox" = account?.environment === "sandbox" ? "sandbox" : "production";
  const market = marketData?.symbol === symbol && marketData.environment === environment && marketData.interval === interval ? marketData : null;
  const digits = displayDigits ?? priceDigits(state?.instrument.price_increment ?? market?.last_price);
  const displayPrice = (value: string | number | null | undefined) => formatPrice(value, digits);
  const selection = `${accountId}:${environment}:${symbol}`;
  const selectionRef = useRef(selection);
  useEffect(() => {selectionRef.current = selection;}, [selection]);
  function rememberSelection(nextSymbol: string, nextInterval: string) {
    try {localStorage.setItem(selectionKey, JSON.stringify({symbol: nextSymbol, interval: nextInterval}));} catch { /* Optional display preference. */ }
  }
  function resetSelection() {setDisplayDigits(null); setLocalChart(null); setChartSource("binance"); setConfirmation(null); setCancelOrder(null); setBatchPlan(null); setProfessionalPlan(null); setSelectedCancels([]); setBatchDraft(""); setStrategyMarkers([]); setLiquidationModel(null); setQuantity(""); setPrice(""); setNotice(""); setState(null); setMarket(null); setAccountError(""); setFills([]); setFillError("");}
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    const initial = window.setTimeout(() => {
      try {
        const saved = restoreTradeSelection(localStorage.getItem(selectionKey));
        setSymbol(saved.symbol); setInterval(saved.interval);
      } catch { /* Default selection remains available without storage. */ }
      setPreferencesReady(true);
      try {const stored = localStorage.getItem(pendingKey); if (stored) setPending(JSON.parse(stored));} catch { /* Runtime remains usable without browser storage. */ }
    }, 0);
    const c = new AbortController();
    tradingRequest<TradingAccount[]>("/api/system/exchange-accounts", {signal: c.signal})
      .then(rows => {setAccounts(rows); setAccountId(rows[0] ? String(rows[0].id) : "");})
      .catch(e => {if (!c.signal.aborted) setAccountError(e.message);});
    return () => {c.abort(); window.clearInterval(timer); window.clearTimeout(initial);};
  }, []);
  useEffect(() => {if (preferencesReady) return subscribeMarket(symbol, environment, interval, setMarket, setMarketError);}, [symbol, environment, interval, preferencesReady]);
  useEffect(() => {
    if (!preferencesReady || environment !== "production") return;
    const c = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      const started = Date.now();
      try {
        const result = await tradingRequest<components["schemas"]["LocalChartResponse"]>(`/api/data/chart?symbol=${symbol.replace(/-PERP$/, "")}&interval=${interval}&product=${product}`, {signal: c.signal});
        if (!c.signal.aborted) {setLocalChart(result); setLocalError("");}
      } catch {if (!c.signal.aborted) setLocalError("后端 K 线读取失败");}
      if (!c.signal.aborted) timer = setTimeout(load, Math.max(0, (chartSource === "local" ? 1000 : 30000) - (Date.now() - started)));
    }
    void load();
    return () => {c.abort(); clearTimeout(timer);};
  }, [symbol, environment, interval, chartSource, preferencesReady, product]);
  const local = environment === "production" && localChart?.symbol === symbol.replace(/-PERP$/, "") && localChart.interval === interval ? localChart : null;
  const showLocal = chartSource === "local" && environment === "production";
  const chartMarket = showLocal ? local && {symbol, candles: local.candles, last_price: local.candles.at(-1)?.close ?? "0"} : market;
  const refreshState = useCallback(async (signal?: AbortSignal) => {
    if (!preferencesReady || !accountId || refreshing.current === selection) return;
    const key = selection;
    refreshing.current = key;
    setLoading(true);
    try {
      const result = await tradingRequest<TradeState>(`/api/trading/accounts/${accountId}/${product === "spot" ? "spot/state" : "state"}?symbol=${symbol}`, {signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(75_000)]) : AbortSignal.timeout(75_000)});
      if (selectionRef.current === key) {setState(result); setAccountError("");}
    } catch (e) {if (!signal?.aborted && selectionRef.current === key) setAccountError(e instanceof Error ? e.message : "账户查询失败。");}
    finally {if (refreshing.current === key) refreshing.current = null; if (!signal?.aborted && selectionRef.current === key) setLoading(false);}
  }, [accountId, symbol, selection, preferencesReady, product]);
  useEffect(() => {
    const c = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      const started = Date.now();
      if (!busyRef.current) await refreshState(c.signal);
      if (!c.signal.aborted) timer = setTimeout(load, Math.max(1000, 20_000 - (Date.now() - started)));
    }
    timer = setTimeout(load, 0);
    function focus() {if (!busyRef.current) void refreshState(c.signal);}
    window.addEventListener("focus", focus);
    return () => {c.abort(); clearTimeout(timer); window.removeEventListener("focus", focus);};
  }, [refreshState]);
  useEffect(() => {
    function escape(e: KeyboardEvent) {if (e.key === "Escape" && !busyRef.current) {setConfirmation(null); setCancelOrder(null); setBatchPlan(null); setProfessionalPlan(null);}}
    document.addEventListener("keydown", escape); return () => document.removeEventListener("keydown", escape);
  }, []);

  useEffect(() => {
    if (table !== "trades" || !accountId) return;
    const c = new AbortController();
    dataManagementApi.operations({accountId, symbol, eventType: "fill", start: "", end: ""}, {limit: "100", environment}, c.signal)
      .then(result => {setFills(result.records); setFillError(result.warnings.join("；"));})
      .catch(e => {if (!c.signal.aborted) setFillError(e.message);});
    return () => c.abort();
  }, [table, accountId, symbol, environment]);


  // Model data is opt-in; the platform never infers market-wide liquidations.
  useEffect(() => {
    if (!preferencesReady || product !== "futures_um") return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await tradingRequest<{model: LiquidationEstimates | null}>(
          `/api/trading/liquidation-model?symbol=${encodeURIComponent(symbol)}`,
          {signal: controller.signal},
        );
        if (!controller.signal.aborted) setLiquidationModel(result.model);
      } catch {
        if (!controller.signal.aborted) setLiquidationModel(null);
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 20_000);
    }
    void poll();
    return () => {controller.abort(); clearTimeout(timer); setLiquidationModel(null);};
  }, [preferencesReady, symbol, product]);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const tenants = await tradingRequest<DevTenant[]>("/api/tenants", {signal: controller.signal});
        const candidates = tenants.filter(item => item.purpose === "development").slice(0, 20);
        const accounts = await Promise.all(candidates.map(async tenant => {
          try {
            const rows = await tradingRequest<VirtualAccount[]>(`/api/tenants/${tenant.id}/accounts`,
              {signal: controller.signal});
            return rows.filter(row => row.kind === "virtual").map(row => ({
              value: `${tenant.id}:${row.id}`, label: `${tenant.name} / ${row.account_name}`,
            }));
          } catch {return [];}
        }));
        if (!controller.signal.aborted) setStrategySources(accounts.flat());
      } catch {if (!controller.signal.aborted) setStrategySources([]);}
    }
    void load();
    return () => controller.abort();
  }, []);

  // User-chosen, tenant-authorized *historical backtest targets*, never execution signals.
  useEffect(() => {
    if (!selectedStrategySource || product !== "futures_um") return;
    const [tenantId, virtualAccountId] = selectedStrategySource.split(":");
    const controller = new AbortController();
    async function load() {
      try {
        const query = `tenant_id=${tenantId}&virtual_account_id=${virtualAccountId}&symbol=${encodeURIComponent(symbol)}`;
        const data = await tradingRequest<{signals:ChartSignal[];message:string}>(
          `/api/trading/chart-signals?${query}`, {signal: controller.signal});
        if (!controller.signal.aborted) {setStrategyMarkers(data.signals);setStrategyMessage(data.message);}
      } catch {
        if (!controller.signal.aborted) setStrategyMessage("授权回测目标暂不可读取；没有叠加任何标记。");
      }
    }
    void load();
    return () => controller.abort();
  }, [selectedStrategySource, symbol, product]);

  function remember(value: Pending | null) {
    setPending(value);
    try {if (value) localStorage.setItem(pendingKey, JSON.stringify(value)); else localStorage.removeItem(pendingKey);} catch { /* Server retains durable identity. */ }
  }
  function prepare(side: "buy" | "sell") {
    if (!account || busyRef.current || disabledReason) return;
    const order: OrderRequest = {account_revision: state!.account_revision, request_id: requestId(), symbol, environment, side, order_type: orderType,
      quantity, price: orderType === "limit" ? price : null,
      ...(product === "futures_um" ? {
        expected_position_mode: state!.position_mode, position_effect: reduceOnly ? "close" : "open",
        reduce_only: reduceOnly,
      } : {}),
      post_only: orderType === "limit" && postOnly,
      time_in_force: orderType === "limit" ? tif : "GTC"};
    const error = orderError(order); if (error) {setNotice(error); return;}
    setConfirmation({accountId: account.id, order});
  }

  function prepareBatchSubmit() {
    if (!account || !state || busyRef.current || disabledReason) return;
    try {
      if (product === "futures_um" && reduceOnly) throw new Error("批量限价仅支持开仓。请先取消只减仓或平仓选项。");
      const lines = parseBatchLimitLines(batchDraft);
      const tasks = lines.map((row, index) => {
        const order: OrderRequest = {
          account_revision: state.account_revision, request_id: requestId(),
          symbol, environment, side: row.side, order_type: "limit",
          price: row.price, quantity: row.quantity, time_in_force: "GTC",
          post_only: false,
          ...(product === "futures_um" ? {
            expected_position_mode: state.position_mode,
            position_effect: "open" as const, reduce_only: false,
          } : {}),
        };
        const error = orderError(order);
        if (error) throw new Error(`第 ${index + 1} 笔：${error}`);
        if (!matchesIncrement(row.price, state.instrument.price_increment)
            || !matchesIncrement(row.quantity, state.instrument.size_increment)) {
          throw new Error(`第 ${index + 1} 笔价格或数量不符合交易所步长。不会自动四舍五入。`);
        }
        if (state.instrument.min_quantity && Number(row.quantity) < Number(state.instrument.min_quantity)) {
          throw new Error(`第 ${index + 1} 笔数量低于交易所最低数量。`);
        }
        if (state.instrument.min_notional && Number(row.price) * Number(row.quantity) < Number(state.instrument.min_notional)) {
          throw new Error(`第 ${index + 1} 笔名义金额低于交易所最低限额。`);
        }
        return {kind: "submit" as const,
          path: `/api/trading/accounts/${account.id}/${product === "spot" ? "spot/" : ""}orders`,
          payload: order};
      });
      setConfirmation(null); setCancelOrder(null);
      setBatchPlan({accountId: account.id, kind: "submit", tasks,
        lines: lines.map((row, i) => `${i + 1}. ${row.side === "buy" ? "买" : "卖"} ${row.quantity} ${base} @ ${row.price} ${quote}`)});
    } catch (error) {setNotice(error instanceof Error ? error.message : "批量委托参数无效。");}
  }

  function prepareBatchCancel() {
    if (!account || !state || busyRef.current || pending) return;
    if (!isFresh(state.checked_at, now) || accountError) {setNotice("请先刷新账户与委托。");return;}
    const orders = state.orders.filter(row => row.open && selectedCancels.includes(row.client_order_id));
    if (!orders.length || orders.length > 20 || orders.length !== selectedCancels.length) {
      setNotice("请选择当前合约 1～20 笔有效的待撤订单，再刷新核对。");return;
    }
    setConfirmation(null);setCancelOrder(null);
    setBatchPlan({accountId: account.id, kind: "cancel",
      tasks: orders.map(order => ({
        kind: "cancel" as const,
        path: `/api/trading/accounts/${account.id}/${product === "spot" ? "spot/" : ""}cancel`,
        payload: {account_revision: state.account_revision, request_id: requestId(),
          symbol, environment, client_order_id: order.client_order_id},
      })),
      lines: orders.map((order, i) => `${i + 1}. ${order.client_order_id} · ${order.side} ${order.quantity} @ ${order.price ?? "市价"}`),
    });
  }

  async function sendBatch() {
    if (!batchPlan || !account || busyRef.current || pending || batchPlan.accountId !== account.id) return;
    if (!state || !isFresh(state.checked_at, now) || accountError
        || (batchPlan.kind === "submit" && (disabledReason || !isFresh(market?.checked_at, now)))) {
      setBatchPlan(null);setNotice("账户或行情快照已过期，请刷新后重新确认批次。");return;
    }
    busyRef.current = true;setBusy(true);setNotice("");
    try {
      const outcome = await executeManualBatch(batchPlan.tasks, requestId => {
        remember(requestId ? {accountId: account.id, requestId, symbol, environment} : null);
      });
      setNotice(`已确认 ${outcome.done}/${batchPlan.tasks.length} 笔 · ${outcome.message}`);
      if (!outcome.uncertain) setSelectedCancels([]);
    } catch (error) {
      setNotice(`批量操作已停止：${error instanceof Error ? error.message : "未知错误"}。请核对交易所状态。`);
    } finally {
      setBatchPlan(null); busyRef.current = false;setBusy(false);void refreshState();
    }
  }


  function prepareConditional(side: "buy" | "sell") {
    if (!account || !state || disabledReason || product !== "futures_um") return;
    const isTrailing = algoType === "TRAILING_STOP_MARKET";
    const isLimit = algoType === "STOP" || algoType === "TAKE_PROFIT";
    if (!matchesIncrement(algoQty, state.instrument.size_increment)
        || state.instrument.min_quantity && Number(algoQty) < Number(state.instrument.min_quantity)) {
      setNotice("条件单数量无效或不符合合约精度。"); return;
    }
    const tick = state.instrument.price_increment;
    if (!isTrailing && !matchesIncrement(triggerPrice, tick)
        || isLimit && !matchesIncrement(stopLimitPrice, tick)
        || isTrailing && activationPrice && !matchesIncrement(activationPrice, tick)) {
      setNotice("触发价/委托价/激活价无效或不符合合约精度。"); return;
    }
    if (isTrailing && (!/^\d+(\.\d{1,4})?$/.test(callbackRate) || Number(callbackRate) < 0.1 || Number(callbackRate) > 20)) {
      setNotice("跟踪回调率必须在 0.1%～20% 之间。"); return;
    }
    const position = state.positions.find(p => p.side === (side === "sell" ? "long" : "short"));
    if (!position || Number(algoQty) > Number(position.quantity)) {
      setNotice("仅允许保护已有仓位的减仓条件单，数量不能超过当前多/空持仓。"); return;
    }
    const payload: AlgoRequest = {
      account_revision: state.account_revision, request_id: requestId(), symbol, environment, side,
      order_type: algoType, quantity: algoQty, expected_position_mode: state.position_mode,
      position_effect: "close", working_type: workingType,
      trigger_price: isTrailing ? null : triggerPrice,
      price: isLimit ? stopLimitPrice : null,
      activation_price: isTrailing && activationPrice ? activationPrice : null,
      callback_rate: isTrailing ? callbackRate : null,
      price_protect: !isTrailing && !isLimit && priceProtect,
    };
    setProfessionalPlan({accountId: account.id, action: "algo_submit", payload,
      title: "交易所原生条件单",
      details: [
        `方向：${side === "sell" ? "卖出平多" : "买入平空"}，${algoQty} ${base}`,
        `类型：${algoType} · ${workingType}`,
        isTrailing ? `回调率：${callbackRate}% · 激活价：${activationPrice || "交易所最新价"}`
          : `触发价：${triggerPrice}${isLimit ? ` · 限价：${stopLimitPrice}` : ""}`,
        "仅对已有仓位减仓；由 Binance 托管触发，非页面价格提醒。",
      ]});
  }

  function prepareAlgoCancel(order: AlgoOrder) {
    if (!account || !state || busyRef.current || pending || !isFresh(state.algo_checked_at, now)) {
      setNotice("请先刷新条件订单，确认交易所最新挂单后撤销。"); return;
    }
    const payload: AlgoCancelRequest = {
      account_revision: state.account_revision, request_id: requestId(), symbol, environment,
      client_algo_id: order.client_algo_id,
    };
    setProfessionalPlan({accountId: account.id, action: "algo_cancel", payload,
      title: "撤销交易所条件单",
      details: [`客户条件单号：${order.client_algo_id}`, `类型：${order.order_type}`,
                `方向：${order.side} · 状态：${order.status}`]});
  }

  function prepareLeverage() {
    if (!account || !state || disabledReason || product !== "futures_um") return;
    const proposed = Number(newLeverage);
    if (!/^\d+$/.test(newLeverage) || !Number.isInteger(proposed) || proposed < 1 || proposed > 125) {
      setNotice("杠杆必须是 1～125 的整数；交易所可能有更低的合约或账户上限。"); return;
    }
    const payload: LeverageRequest = {account_revision: state.account_revision, request_id: requestId(),
      symbol, environment, leverage: proposed};
    setProfessionalPlan({accountId: account.id, action: "leverage", payload,
      title: "更改合约初始杠杆",
      details: [`合约：${symbol}`, `当前：${state.leverage ?? "未知"}× → 目标：${proposed}×`,
                "不会切换保证金模式或持仓模式；交易所决定该账户可用上限。"]});
  }

  async function sendProfessional() {
    if (!professionalPlan || !account || !state || busyRef.current || pending ||
        professionalPlan.accountId !== account.id) return;
    if (disabledReason || !isFresh(state.checked_at, now)) {
      setProfessionalPlan(null); setNotice("账户或行情快照已过期，请重新核对后提交。");return;
    }
    busyRef.current = true; setBusy(true); setNotice("");
    const payload = professionalPlan.payload;
    remember({accountId: account.id, requestId: payload.request_id, symbol, environment});
    const suffix = professionalPlan.action === "algo_submit" ? "algo/orders"
      : professionalPlan.action === "algo_cancel" ? "algo/cancel" : "leverage";
    try {
      const result = await tradingRequest<Result>(`/api/trading/accounts/${account.id}/${suffix}`, {
        method: "POST", body: JSON.stringify(payload),
      });
      setNotice(`${result.message}${result.algo_order ? ` · ${result.algo_order.client_algo_id}` : ""}`);
      if (result.status === "completed") remember(null);
      if (professionalPlan.action === "algo_submit" && result.status === "completed") setAlgoQty("");
    } catch (error) {
      setNotice(`操作结果待核实：${error instanceof Error ? error.message : "连接中断"}。不得盲目重新提交。`);
    } finally {
      setProfessionalPlan(null); busyRef.current = false; setBusy(false); void refreshState();
    }
  }

  async function send() {
    if (busyRef.current || !account || (!confirmation && !cancelOrder)) return;
    busyRef.current = true; setBusy(true); setNotice("");
    const id = confirmation?.order.request_id ?? requestId();
    const value = {accountId: account.id, requestId: id, symbol, environment};
    remember(value);
    try {
      const result = await tradingRequest<Result>(`/api/trading/accounts/${value.accountId}/${product === "spot" ? "spot/" : ""}${cancelOrder ? "cancel" : "orders"}`, {
        method: "POST", body: JSON.stringify(cancelOrder ? {account_revision: state!.account_revision, request_id: id, symbol, environment, client_order_id: cancelOrder.client_order_id} : confirmation?.order),
      });
      setNotice(`${result.message}${result.client_order_id ? ` · ${result.client_order_id}` : ""}`);
      if (result.status === "completed") {remember(null); setQuantity("");}
    } catch (e) {setNotice(`结果待核实：${e instanceof Error ? e.message : "网络中断"}。请核对委托，不要重复提交。`);}
    finally {setConfirmation(null); setCancelOrder(null); busyRef.current = false; setBusy(false); void refreshState();}
  }
  async function checkPending() {
    if (!pending || busyRef.current) return;
    setBusy(true); busyRef.current = true;
    try {
      const result = await tradingRequest<Result>(`/api/trading/accounts/${pending.accountId}/requests/${pending.requestId}?environment=${pending.environment}`);
      setNotice(result.message);
      if (result.status === "completed") remember(null);
    } catch (e) {setNotice(e instanceof Error ? e.message : "暂时无法核对委托。");}
    finally {busyRef.current = false; setBusy(false); void refreshState();}
  }
  const quote = symbol.endsWith("FDUSD") ? "FDUSD" : symbol.replace(/-PERP$/, "").endsWith("USDC") ? "USDC" : "USDT";
  const base = symbol.replace(/(?:USDT|USDC|FDUSD)(?:-PERP)?$/, "");
  const disabledReason = orderBlockReason({hasAccount: preferencesReady && Boolean(account), accountCheckedAt: state?.checked_at,
    marketCheckedAt: market?.checked_at, now, busy, pending: Boolean(pending), loading, accountError, marketError});
  const ready = !disabledReason;
  const openOrders = state?.orders.filter(o => o.open) ?? [];
  const history = state?.orders.filter(o => !o.open) ?? [];
  const algoOrders = state?.algo_orders?.filter(o => o.open) ?? [];
  const spotBalances = Object.entries(state?.balances ?? {})
    .filter(([, amount]) => Number(amount.total) > 0)
    .sort(([a], [b]) => a.localeCompare(b));
  async function toggleFullscreen() {
    if (expanded) {if (document.fullscreenElement) await document.exitFullscreen(); setExpanded(false);}
    else {setExpanded(true); try {await page.current?.requestFullscreen();} catch { /* In-app expansion works when native fullscreen is unavailable. */ }}
  }
  useEffect(() => {
    function sync() {setExpanded(Boolean(document.fullscreenElement));}
    function escape(e: KeyboardEvent) {if (e.key === "Escape" && !document.fullscreenElement) setExpanded(false);}
    document.addEventListener("fullscreenchange",sync);document.addEventListener("keydown",escape);
    return()=>{document.removeEventListener("fullscreenchange",sync);document.removeEventListener("keydown",escape);};
  },[]);
  return <section ref={page} className={`trade-page${expanded ? " trade-expanded" : ""}`} aria-label="手动交易">
    <div className="trade-ticker"><div className="trade-selectors">
      <div className="trade-selector"><span>平台</span><select aria-label="交易平台" disabled={busy}><option>Binance</option></select></div>
      <div className="trade-selector"><span>产品</span><select aria-label="交易产品" value={product} disabled={busy} onChange={e=>{const value=e.target.value === "spot" ? "BTCUSDT" : "BTCUSDT-PERP";resetSelection();setSymbol(value);rememberSelection(value,interval);}}><option value="futures_um">USD-M 永续</option><option value="spot">现货</option></select></div>
      <div className="trade-selector"><span>账户</span><select aria-label="交易账户" value={accountId} disabled={busy} onChange={e => {resetSelection(); setAccountId(e.target.value);}}><option value="">选择账户</option>{accounts.map(a => <option key={a.id} value={a.id}>{a.account_name} · {a.environment === "sandbox" ? "测试网" : "实盘"}</option>)}</select></div>

    </div><MarketPicker symbol={symbol} environment={environment} product={product} disabled={busy} onSelect={value=>{resetSelection();setSymbol(value);rememberSelection(value, interval);}} />
      <div className="trade-latest"><strong className={Number(market?.change_percent)>=0?"trade-green":"trade-red"}>{market ? Number(market.last_price).toLocaleString("en-US",{minimumFractionDigits:digits,maximumFractionDigits:digits}) : "—"}</strong><small className={Number(market?.change_percent)>=0?"trade-green":"trade-red"}>{market?.change_price ? `${Number(market.change_price)>0?"+":""}${displayPrice(market.change_price)}` : "—"} {market ? `${Number(market.change_percent)>=0?"+":""}${number(market.change_percent,2)}%` : "—"}</small></div>
      {product === "futures_um" && [["标记价格", displayPrice(market?.mark_price)], ["指数价格", displayPrice(market?.index_price)]].map(([label,value])=><div className="trade-stat" key={label}><span>{label}</span><b>{value}</b></div>)}
      {product === "futures_um" && <div className="trade-funding trade-stat"><span>资金费率{market?.funding_interval_hours ? ` (${market.funding_interval_hours}时)` : ""}/倒计时</span><b><em>{market ? `${(Number(market.funding_rate)*100).toFixed(5)}%` : "—"}</em> / {fundingCountdown(market?.next_funding_time, now)}</b></div>}
      {[["24h最高价",displayPrice(market?.high)],["24h最低价",displayPrice(market?.low)],[`24h 成交量(${base})`,number(market?.base_volume,3)],[`24h 成交额(${quote})`,number(market?.quote_volume,2)],[`合约持仓量(${quote})`,product === "futures_um" && market?.open_interest !== undefined && isFresh(market.open_interest_checked_at,now) ? number(String(Number(market.open_interest)*Number(market.mark_price)),2) : "—"]].map(([label,value])=><div className="trade-stat" key={label}><span>{label}</span><b>{value}</b></div>)}
      <button className="trade-fullscreen" aria-label={expanded ? "退出全屏" : "全屏交易"} onClick={()=>void toggleFullscreen()}>{expanded ? "⤡ 退出全屏" : "⤢ 全屏"}</button>
    </div>
    {(marketError || accountError || notice || showLocal && localError || strategyMessage) && <div className="trade-notice" role="status">{[marketError, accountError, showLocal ? localError : "", notice, selectedStrategySource ? strategyMessage : ""].filter(Boolean).join(" · ")}</div>}
    {pending && <div className="trade-pending" role="alert"><span>有一笔操作结果待核实 · {pending.symbol} · {pending.requestId}</span><button disabled={busy} onClick={() => void checkPending()}>核对交易结果</button></div>}
    <div className="trade-layout">
      <section className="trade-chart-panel"><MarketChart interval={interval} periods={<div className="trade-chart-periods">{intervals.map(i => <button key={i} className={interval === i ? "active" : ""} onClick={() => {setMarket(null); setLocalChart(null); setInterval(i);rememberSelection(symbol, i);}}>{i}</button>)}</div>} toolbar={<><label className="trade-price-precision"><span>价格精度</span><select aria-label="价格显示精度" value={digits} onChange={e=>setDisplayDigits(Number(e.target.value))}>{Array.from({length:13},(_,i)=><option key={i} value={i}>{i===0?"1":`0.${"0".repeat(i-1)}1`}</option>)}</select></label><label className="trade-source-select"><span>回测目标</span><select aria-label="回测目标来源（只读）" value={selectedStrategySource} onChange={e => {setSelectedStrategySource(e.target.value);setStrategyMarkers([]);setStrategyMessage("");}}><option value="">不叠加</option>{strategySources.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>{environment === "production" && (local?.candles.length || showLocal) ? <div className="trade-source-select"><select aria-label="K线数据来源" title="K线数据来源" disabled={busy} value={showLocal ? "local" : "binance"} onChange={e => {setConfirmation(null); setChartSource(e.target.value === "local" ? "local" : "binance");}}><option value="binance">实时 K线</option><option value="local">后端 K线</option></select><span aria-hidden="true">▾</span></div> : null}</>} displayDigits={digits} market={chartMarket} depth={market} modeledLiquidations={liquidationModel} strategySignals={strategyMarkers} identity={`${environment}:${symbol}:${interval}:${showLocal ? "local" : "binance"}`} state={isFresh(state?.checked_at, now) && !accountError ? state : null} preview={orderType === "limit" ? price : ""} onPrice={value=>{if (!busy) {setPrice(value);setOrderType("limit");}}} onNotice={setNotice} /></section>
      <section className="trade-book"><div className="trade-panel-tabs"><button className={bookTab === "book" ? "active" : ""} onClick={() => setBookTab("book")}>订单簿</button><button className={bookTab === "trades" ? "active" : ""} onClick={() => setBookTab("trades")}>最新成交</button></div><div className="trade-book-head"><span>价格({quote})</span><span>数量({base})</span><span>{bookTab === "book" ? "金额" : "时间"}</span></div>
        {bookTab === "book" ? <><div className="trade-book-rows">{market?.asks.slice(0, 9).reverse().map(([p,q]) => <button key={p} className="trade-book-row" onClick={() => setPrice(p)}><span className="trade-red">{displayPrice(p)}</span><span>{q}</span><span>{number(String(Number(p)*Number(q)), 2)}</span></button>)}</div><div className="trade-book-price">{displayPrice(market?.last_price)}{product === "futures_um" && <small>标记 {displayPrice(market?.mark_price)}</small>}</div><div className="trade-book-rows">{market?.bids.slice(0,9).map(([p,q]) => <button key={p} className="trade-book-row" onClick={() => setPrice(p)}><span className="trade-green">{displayPrice(p)}</span><span>{q}</span><span>{number(String(Number(p)*Number(q)), 2)}</span></button>)}</div></> : <div className="trade-recent">{market?.trades.slice().reverse().map(t => <div className="trade-book-row" key={t.id}><span className={t.isBuyerMaker ? "trade-red" : "trade-green"}>{displayPrice(t.price)}</span><span>{t.qty}</span><span>{clock(t.time)}</span></div>)}</div>}
      </section>
      <aside className="trade-ticket"><div className="trade-ticket-title"><h2>下单</h2><span>{environment === "sandbox" ? "测试网" : "实盘"}</span></div>{product === "futures_um" ? <p className="trade-muted">杠杆倍数：{isFresh(state?.checked_at, now) && !accountError && state?.leverage ? `${state.leverage}×` : "—"}</p> : <p className="trade-muted">现货交易无合约杠杆、资金费率或裸卖空</p>}
        <div className="trade-panel-tabs trade-professional-tabs">
          <button className={tradeMode === "standard" ? "active" : ""} disabled={busy} onClick={()=>setTradeMode("standard")}>常规</button>
          {product === "futures_um" && <button className={tradeMode === "conditional" ? "active" : ""} disabled={busy} onClick={()=>setTradeMode("conditional")}>止盈止损/条件</button>}
          {product === "futures_um" && <button className={tradeMode === "leverage" ? "active" : ""} disabled={busy} onClick={()=>setTradeMode("leverage")}>杠杆</button>}
        </div>
        {(tradeMode === "standard" || product === "spot") && <>
        <div className="trade-panel-tabs"><button className={orderType === "limit" ? "active" : ""} onClick={() => setOrderType("limit")} disabled={busy}>限价单</button><button className={orderType === "market" ? "active" : ""} onClick={() => setOrderType("market")} disabled={busy}>市价单</button></div>
        <div className="trade-available"><span>可用余额</span><b>{number(state?.balances[quote]?.free)} {quote}</b></div>
        {product === "spot" && <div className="trade-available"><span>可卖数量</span><b>{number(state?.balances[base]?.free)} {base}</b></div>}
        {orderType === "limit" ? <label>价格<div className="trade-input"><input aria-label="委托价格" inputMode="decimal" value={price} disabled={busy} placeholder="输入委托价格" onChange={e => setPrice(e.target.value)} /><button onClick={() => setPrice(market?.last_price ?? "")} disabled={busy}>最新</button><span>{quote}</span></div></label> : <div className="trade-market-price">以市场最优价格成交</div>}
        <label>数量<div className="trade-input"><input aria-label="委托数量" inputMode="decimal" value={quantity} disabled={busy} placeholder="输入数量" onChange={e => setQuantity(e.target.value)} /><span>{base}</span></div></label>
        <div className="trade-estimate"><span>预估名义金额</span><b>{quantity && (orderType === "limit" ? price : market?.last_price) ? number(String(Number(quantity)*Number(orderType === "limit" ? price : market?.last_price)), 2) : "—"} {quote}</b></div>
        {orderType === "limit" && <><label>有效方式<select value={tif} disabled={busy || postOnly} onChange={e => setTif(e.target.value as "GTC" | "IOC" | "FOK")}><option>GTC</option><option>IOC</option><option>FOK</option></select></label><label className="trade-checkbox"><input type="checkbox" checked={postOnly} disabled={busy} onChange={e => {setPostOnly(e.target.checked); setTif("GTC");}} />只做 Maker (Post Only)</label></>}
        {product === "futures_um" && <label className="trade-checkbox"><input type="checkbox" checked={reduceOnly} disabled={busy} onChange={e => setReduceOnly(e.target.checked)} />{state?.position_mode === "hedge" ? "平仓（自动匹配多 / 空仓位）" : "只减仓 (Reduce Only)"}</label>}
        <div className="trade-order-buttons"><button className="trade-buy" disabled={!ready} onClick={() => prepare("buy")}>{product === "spot" ? "买入现货" : reduceOnly ? "买入 / 平空" : "买入 / 做多"}</button><button className="trade-sell" disabled={!ready} onClick={() => prepare("sell")}>{product === "spot" ? "卖出现货" : reduceOnly ? "卖出 / 平多" : "卖出 / 做空"}</button></div>
        <div className="trade-batch-entry">
          <h3>批量限价委托</h3>
          <p className="trade-muted">每行：买/卖,价格,数量（最多 10 笔）。逐笔发送，非原子批次；仅限 GTC 开仓，任何拒绝或未知结果立即停止。</p>
          <textarea aria-label="批量限价委托清单" rows={4} value={batchDraft} disabled={busy} placeholder={"买,2000,0.01\n卖,2100,0.01"} onChange={event => setBatchDraft(event.target.value)} />
          <button className="trade-batch-action" disabled={!ready || !batchDraft.trim()} onClick={prepareBatchSubmit}>预览批量限价单</button>
        </div>
        </>}
        {product === "futures_um" && tradeMode === "conditional" && <div className="trade-professional-form">
          <p className="trade-muted">由 Binance 托管触发。仅支持对当前真实仓位做保护性减仓，不是前端价格提醒；下单前会重新核对账户与仓位。</p>
          <label>条件单类型<select aria-label="条件单类型" value={algoType} disabled={busy} onChange={e=>setAlgoType(e.target.value as AlgoRequest["order_type"])}>
            <option value="STOP_MARKET">止损市价 STOP_MARKET</option>
            <option value="TAKE_PROFIT_MARKET">止盈市价 TAKE_PROFIT_MARKET</option>
            <option value="STOP">止损限价 STOP</option>
            <option value="TAKE_PROFIT">止盈限价 TAKE_PROFIT</option>
            <option value="TRAILING_STOP_MARKET">跟踪止损 TRAILING_STOP_MARKET</option>
          </select></label>
          <label>减仓数量<input aria-label="条件单数量" inputMode="decimal" disabled={busy} value={algoQty} onChange={e=>setAlgoQty(e.target.value)} /></label>
          {algoType === "TRAILING_STOP_MARKET" ? <>
            <label>回调率（%）<input aria-label="跟踪回调率" inputMode="decimal" disabled={busy} value={callbackRate} onChange={e=>setCallbackRate(e.target.value)} /></label>
            <label>激活价（可留空）<input aria-label="跟踪激活价" inputMode="decimal" disabled={busy} value={activationPrice} onChange={e=>setActivationPrice(e.target.value)} /></label>
          </> : <>
            <label>触发价格<input aria-label="条件触发价格" inputMode="decimal" disabled={busy} value={triggerPrice} onChange={e=>setTriggerPrice(e.target.value)} /></label>
            {(algoType === "STOP" || algoType === "TAKE_PROFIT") && <label>触发后限价<input aria-label="条件单限价" inputMode="decimal" disabled={busy} value={stopLimitPrice} onChange={e=>setStopLimitPrice(e.target.value)} /></label>}
          </>}
          <label>触发价格来源<select aria-label="条件触发来源" value={workingType} disabled={busy} onChange={e=>setWorkingType(e.target.value as "MARK_PRICE" | "CONTRACT_PRICE")}><option value="MARK_PRICE">标记价格</option><option value="CONTRACT_PRICE">最新成交价格</option></select></label>
          {(algoType === "STOP_MARKET" || algoType === "TAKE_PROFIT_MARKET") && <label className="trade-checkbox"><input type="checkbox" checked={priceProtect} disabled={busy} onChange={e=>setPriceProtect(e.target.checked)} />启用交易所价格保护</label>}
          <div className="trade-order-buttons"><button className="trade-buy" disabled={!ready || !state?.positions.some(p=>p.side==="short")} onClick={()=>prepareConditional("buy")}>买入 / 平空</button><button className="trade-sell" disabled={!ready || !state?.positions.some(p=>p.side==="long")} onClick={()=>prepareConditional("sell")}>卖出 / 平多</button></div>
          <p className="trade-muted">原生条件单与普通委托分开展示。触发后订单可能变为成交、拒绝或过期，请核对实际交易所状态。</p>
        </div>}
        {product === "futures_um" && tradeMode === "leverage" && <div className="trade-professional-form">
          <p className="trade-muted">修改合约初始杠杆，不会切换逐仓/全仓或单向/双向持仓。具体上限由交易所返回。</p>
          <label>目标杠杆（1～125×）<input aria-label="目标杠杆" inputMode="numeric" disabled={busy} value={newLeverage} onChange={e=>setNewLeverage(e.target.value)} /></label>
          <button className="trade-batch-action" disabled={!ready} onClick={prepareLeverage}>预览杠杆修改</button>
        </div>}
        {!ready && <p className="trade-muted" role="status">{disabledReason}</p>}
        <div className="trade-contract-info"><h3>{product === "spot" ? "现货交易规则" : "合约信息"}</h3><p><span>价格步长</span><b>{state?.instrument.price_increment ?? "—"}</b></p><p><span>数量步长</span><b>{state?.instrument.size_increment ?? "—"}</b></p><p><span>最小名义金额</span><b>{state?.instrument.min_notional ?? "—"} {quote}</b></p></div>
      </aside>
      <section className="trade-account"><div className="trade-account-head"><div className="trade-panel-tabs">{([['positions', product === "spot" ? `资产(${spotBalances.length})` : `持仓(${state?.positions.length ?? 0})`], ['orders', `当前委托(${openOrders.length})`], ['algo', `条件订单(${algoOrders.length})`], ['history', '历史委托'], ['trades', '成交记录']] as const).map(([id,label]) => <button className={table === id ? "active" : ""} key={id} onClick={() => setTable(id)}>{label}</button>)}</div><div className="trade-account-actions">{table === "orders" && <button disabled={busy || Boolean(pending) || !selectedCancels.length} onClick={prepareBatchCancel}>批量撤单 ({selectedCancels.length})</button>}<button disabled={loading || busy || !account} onClick={() => void refreshState()}>{loading ? "查询中…" : "刷新账户"}</button></div></div>
        <div className="trade-table-wrap">{table === "positions" ? product === "spot" ? <table><thead><tr><th>现货资产</th><th>可用</th><th>锁定</th><th>合计</th></tr></thead><tbody>{spotBalances.map(([currency, balance]) => <tr key={currency}><td>{currency}</td><td>{balance.free}</td><td>{balance.locked}</td><td>{balance.total}</td></tr>)}</tbody></table> : <table><thead><tr><th>合约</th><th>方向</th><th>数量</th><th>开仓均价</th><th>操作</th></tr></thead><tbody>{state?.positions.map(p => <tr key={p.id}><td>{p.symbol}</td><td className={p.side === "long" ? "trade-green" : "trade-red"}>{p.side === "long" ? "多" : "空"}</td><td>{p.quantity}</td><td>{displayPrice(p.entry_price)}</td><td><button disabled={busy} onClick={() => {setQuantity(p.quantity); setOrderType("market"); setReduceOnly(true); setNotice(`已填入平仓数量，请点击${p.side === "long" ? "卖出" : "买入"}并确认。`);}}>填入平仓数量</button></td></tr>)}</tbody></table> : table === "algo" ? <><table>
          <thead><tr><th>时间</th><th>条件单 ID</th><th>类型</th><th>方向/腿</th><th>数量</th><th>触发价/限价</th><th>回调率</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>{(state?.algo_orders ?? []).map(order => <tr key={order.client_algo_id}>
            <td>{order.timestamp ? clock(order.timestamp) : "—"}</td>
            <td>{order.symbol}<small>{order.client_algo_id}</small></td>
            <td>{order.order_type}</td>
            <td>{order.side} / {order.position_side}</td>
            <td>{order.quantity}</td>
            <td>{order.trigger_price ?? order.activation_price ?? "—"} / {order.price ?? "市价"}</td>
            <td>{order.callback_rate ? order.callback_rate + "%" : "—"}</td>
            <td>{order.status}</td><td>{order.open ? <button disabled={busy || Boolean(pending)} onClick={()=>prepareAlgoCancel(order)}>撤条件单</button> : "—"}</td>
          </tr>)}</tbody></table>
          {state?.algo_error && <p className="trade-muted" role="alert">{state.algo_error}</p>}
          {state?.algo_orders && !state.algo_orders.length && <div className="trade-table-empty">交易所当前未返回条件单；历史列表受查询范围限制。</div>}
          </> : table === "trades" ? <><table><thead><tr><th>时间</th><th>合约</th><th>方向</th><th>成交价格</th><th>成交数量</th><th>手续费</th></tr></thead><tbody>{fills.map(f => <tr key={f.id}><td>{f.timestamp ? clock(f.timestamp) : "—"}</td><td>{f.symbol}</td><td className={f.side === "buy" ? "trade-green" : "trade-red"}>{f.side}</td><td>{displayPrice(f.price)}</td><td>{f.amount ?? "—"}</td><td>{f.fee_cost ?? "—"} {f.fee_currency}</td></tr>)}</tbody></table>{!fills.length && <div className="trade-table-empty">当前合约暂无已入账成交</div>}{fillError && <p className="trade-muted">{fillError}</p>}</> : <table><thead><tr><th>时间</th><th>合约 / 客户单号</th><th>类型</th><th>方向</th><th>价格</th><th>数量 / 已成交</th><th>状态</th>{table === "orders" && <><th>选择</th><th>操作</th></>}</tr></thead><tbody>{(table === "orders" ? openOrders : history).map(o => <tr key={o.client_order_id}><td>{clock(o.timestamp)}</td><td>{o.symbol}<small>{o.client_order_id}</small></td><td>{o.order_type}</td><td className={o.side === "buy" ? "trade-green" : "trade-red"}>{o.side}</td><td>{o.price === null ? "市价" : displayPrice(o.price)}</td><td>{o.quantity} / {o.filled}</td><td>{o.status}</td>{table === "orders" && <><td><input aria-label={`选择撤销 ${o.client_order_id}`} type="checkbox" checked={selectedCancels.includes(o.client_order_id)} disabled={busy || Boolean(pending)} onChange={e => setSelectedCancels(current => e.target.checked ? [...current.filter(id => id !== o.client_order_id), o.client_order_id] : current.filter(id => id !== o.client_order_id))} /></td><td><button disabled={busy || Boolean(pending)} onClick={() => setCancelOrder(o)}>撤单</button></td></>}</tr>)}</tbody></table>}
        {table !== "trades" && !(table === "positions" ? (product === "spot" ? spotBalances.length : state?.positions.length) : table === "orders" ? openOrders.length : table === "algo" ? 1 : history.length) && <div className="trade-table-empty">{loading ? "正在查询交易所账户…" : !state ? "账户数据尚未就绪" : "当前合约暂无记录"}</div>}</div>

      </section>
    </div>
    {professionalPlan && <div className="trade-modal-backdrop"><section className="trade-confirm" role="dialog" aria-modal="true" aria-labelledby="trade-professional-confirm-title">
      <h2 id="trade-professional-confirm-title">{professionalPlan.title}</h2>
      <p className="trade-confirm-environment">{account?.account_name} · {environment === "sandbox" ? "测试网" : "真实账户"} · {symbol}</p>
      <ul className="trade-professional-review">{professionalPlan.details.map((item,index)=><li key={index}>{item}</li>)}</ul>
      <p>{environment === "production" ? "确认后将向 Binance 实盘账户发送不可自动撤回的交易所指令。" : "确认后将向 Binance 测试网发送操作。"}</p>
      <p>若请求结果未知，必须先通过交易所和请求编号核对，禁止再次发送。</p>
      <div className="trade-confirm-actions"><button disabled={busy} onClick={()=>setProfessionalPlan(null)}>返回修改</button><button disabled={busy} onClick={()=>void sendProfessional()}>{busy ? "处理中…" : "确认提交"}</button></div>
    </section></div>}
    {batchPlan && <div className="trade-modal-backdrop"><section className="trade-confirm" role="dialog" aria-modal="true" aria-labelledby="trade-batch-confirm-title">
      <h2 id="trade-batch-confirm-title">确认{batchPlan.kind === "submit" ? "批量限价委托" : "批量撤单"}（{batchPlan.tasks.length} 笔）</h2>
      <p className="trade-confirm-environment">{account?.account_name} · {environment === "sandbox" ? "测试网" : "真实账户"} · {symbol}</p>
      <ol className="trade-batch-review">{batchPlan.lines.map((line, index) => <li key={index}>{line}</li>)}</ol>
      <p>各笔操作独立发送；前一笔收到明确成功结果后才会提交下一笔。交易所拒绝、网络中断或结果未知时停止，已成交的操作不会自动回滚。</p>
      {environment === "production" && <p><strong>警告：</strong>确认将向真实 Binance 账户逐笔发送交易指令。</p>}
      <div className="trade-confirm-actions"><button disabled={busy} onClick={() => setBatchPlan(null)}>返回修改</button><button disabled={busy} onClick={() => void sendBatch()}>{busy ? "提交中…" : "确认逐笔发送"}</button></div>
    </section></div>}
    {(confirmation || cancelOrder) && <div className="trade-modal-backdrop"><section className="trade-confirm" role="dialog" aria-modal="true" aria-labelledby="trade-confirm-title"><h2 id="trade-confirm-title">{cancelOrder ? "确认撤销委托" : "确认提交订单"}</h2><p className="trade-confirm-environment">{account?.account_name} · {environment === "sandbox" ? "测试网" : "真实账户"}</p><dl>{Object.entries(confirmation ? {合约: symbol, 方向: confirmation.order.side === "buy" ? "买入" : "卖出", 类型: confirmation.order.order_type === "limit" ? "限价" : "市价", 数量: `${confirmation.order.quantity} ${base}`, 价格: confirmation.order.price ?? "市场价格", ...(product === "futures_um" ? {持仓模式: confirmation.order.expected_position_mode === "hedge" ? "双向 · 沿用账户" : "单向 · 沿用账户", 只减仓: confirmation.order.reduce_only ? "是" : "否"} : {})} : {合约: symbol, 客户订单号: cancelOrder!.client_order_id, 价格: cancelOrder!.price ?? "市价", 数量: cancelOrder!.quantity}).map(([k,v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl><p>{environment === "production" ? "确认后将向 Binance 实盘账户发送交易指令。" : "确认后向 Binance 测试网发送指令。"}</p><div className="trade-confirm-actions"><button disabled={busy} onClick={() => {setConfirmation(null); setCancelOrder(null);}}>返回修改</button><button disabled={busy} onClick={() => void send()}>{busy ? "提交中…" : "确认"}</button></div></section></div>}
  </section>;
}
