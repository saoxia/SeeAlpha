"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  dataRequest, selection, dataJobLabels, dataStateLabels, dataLevelLabels,
  groupDataItems, subscriptionState, realtimeStatus, hasPairTask, taskTransitions, managedItems, dataProductLabels, dataQuality,
  type ConfigurationPlan, type DataProduct, type DataMarket, type DataCapabilities, type DataDeletionPlan, type DataInventory, type DataItem, type DataJob,
  type PairDeletePlanRequest, type PairDeleteRequest, type DataPolicy, type Subscription, type Subscriptions, type DataPlan, type DataSelection, type DataStatus, type FeedStatus,
} from "@/app/features/data-management/market-data-api";

import { shiftDay } from "@/app/features/data-management/date-range";

type DialogMode = "configure" | "prepare" | "delete_pair";
const emptyTasks: DataJob[] = [];
const dialogTitles: Record<DialogMode, string> = {
  configure: "交易对数据配置", prepare: "获取行情数据",
  delete_pair: "删除交易对",
};
function errorMessage(error: unknown) { return error instanceof Error ? error.message : "数据操作失败。"; }
function displayBytes(value: number) { return value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KB` : value < 1024 ** 3 ? `${(value / 1024 ** 2).toFixed(1)} MB` : `${(value / 1024 ** 3).toFixed(2)} GB`; }
function eventDate(value: string) { return new Date(Number(BigInt(value) / BigInt(1_000_000))).toISOString().replace("T", " ").replace("Z", " UTC"); }

export function MarketDataPanel() {
  const [capabilities, setCapabilities] = useState<DataCapabilities | null>(null);
  const [status, setStatus] = useState<DataStatus | null>(null);
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [baseLevel, setBaseLevel] = useState("1m");
  const [realtimeEnabled, setRealtimeEnabled] = useState(true);
  const [historyEnabled, setHistoryEnabled] = useState(false);
  const [historyStart, setHistoryStart] = useState("");
  const [feed, setFeed] = useState<FeedStatus | null>(null);
  const [inventory, setInventory] = useState<DataInventory | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [filterProduct, setFilterProduct] = useState("");
  const [filterSymbol, setFilterSymbol] = useState("");
  const [filterLevel, setFilterLevel] = useState("");
  const [filterState, setFilterState] = useState("");
  const [mode, setMode] = useState<DialogMode | null>(null);
  const [adding, setAdding] = useState(false);
  const [symbol, setSymbol] = useState("");
  const [product, setProduct] = useState("");
  const [optionUnderlying, setOptionUnderlying] = useState("");
  const [optionExpiry, setOptionExpiry] = useState("");
  const [optionRight, setOptionRight] = useState("");
  const [markets, setMarkets] = useState<DataMarket[]>([]);
  const [marketSearch, setMarketSearch] = useState("");
  const [marketError, setMarketError] = useState("");
  const [marketsLoading, setMarketsLoading] = useState(false);
  const [marketRetry, setMarketRetry] = useState(0);
  const [dataLevel, setDataLevel] = useState("tick");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [plan, setPlan] = useState<{ key: string; value: DataPlan } | null>(null);
  const [deletion, setDeletion] = useState<{ key: string; value: DataDeletionPlan } | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [configurationPlan, setConfigurationPlan] = useState<{ key: string; value: ConfigurationPlan } | null>(null);
  const [taskList, setTaskList] = useState<DataJob[] | null>(null);
  const seenTasks = useRef<Map<string, string> | null>(null);
  const [message, setMessage] = useState("");
  const [pollErrors, setPollErrors] = useState<Record<string, string>>({});
  const pollError = Object.values(pollErrors).filter(Boolean).join("；");
  const [busy, setBusy] = useState(false);
  const [submissionStage, setSubmissionStage] = useState("");
  const submissionFeedback = useRef<HTMLDivElement>(null);
  const key = JSON.stringify({ product, symbol, start, end, dataLevel });
  const deletionKey = JSON.stringify({ product, symbol });
  const currentPlan = plan?.key === key ? plan.value : null;
  const currentDeletion = deletion?.key === deletionKey ? deletion.value : null;
  const currentProduct: DataProduct = product === "options" ? "options" : "futures_um";
  const configurationKey = JSON.stringify({ symbol, currentProduct, baseLevel, realtimeEnabled, historyEnabled, historyStart });
  const currentConfiguration = configurationPlan?.key === configurationKey ? configurationPlan.value : null;
  const levelLabel = currentProduct === "options" && dataLevel === "tick" ? "近期逐笔 Tick" : dataLevelLabels[dataLevel] ?? dataLevel;
  const levels = ["tick", ...(currentProduct === "options" ? capabilities?.option_intervals ?? ["1m", "5m", "15m"] : capabilities?.intervals ?? [])];
  const tasks = taskList ?? emptyTasks;
  const items = managedItems(inventory?.items ?? [], feed, tasks);
  const grouped = groupDataItems(items, subscriptions);
  const symbols = [...new Set(grouped.filter((item) => !filterProduct || (item.product ?? "futures_um") === filterProduct).map((item) => item.symbol))];
  const underlyings = [...new Set(markets.map((item) => item.underlying).filter((value): value is string => Boolean(value)))];
  const expiries = [...new Set(markets.filter((item) => item.underlying === optionUnderlying).map((item) => item.expiry).filter((value): value is number => typeof value === "number"))].sort((a, b) => a - b);
  const marketOptions = markets.filter((item) => (currentProduct !== "options" || (Boolean(optionUnderlying) && item.underlying === optionUnderlying
    && (!optionExpiry || String(item.expiry) === optionExpiry) && (!optionRight || item.right === optionRight)))
    && item.symbol.includes(marketSearch.trim().toUpperCase()));
  const selectedItem = items.find((item) => item.symbol === symbol && item.level === dataLevel && (item.product ?? "futures_um") === currentProduct)
    ?? (symbol ? { symbol, product: currentProduct, level: dataLevel, event_ranges_ns: [], quality: "unverified" } : undefined);
  const canWrite = Boolean(capabilities?.can_operate && status?.storage.available && status.storage.writable && !pollError);
  const pairBusy = hasPairTask(tasks, symbol, currentProduct);
  const disabled = busy || pairBusy || !canWrite;
  function hasHistory(item: DataItem) {
    return items.some((value) => value.symbol === item.symbol && (value.product ?? "futures_um") === (item.product ?? "futures_um") && value.event_ranges_ns.length > 0);
  }
  function liveStatus(item: DataItem) {
    const configured = subscriptions.find((value) => value.policy.symbol === item.symbol && value.policy.product === (item.product ?? "futures_um"));
    return configured ? subscriptionState(configured, now) : realtimeStatus(feed, item.symbol, item.level, now, item.product);
  }
  const visible = grouped.filter((item) => (!filterProduct || (item.product ?? "futures_um") === filterProduct) && (!filterSymbol || item.symbol === filterSymbol)
    && (!filterLevel || item.level === filterLevel || item.levels.includes(filterLevel))
    && (!filterState || (filterState === "stored" ? hasHistory(item)
      : filterState === "empty" ? !hasHistory(item)
      : liveStatus(item) === "运行中")));

  const refresh = useCallback(async (signal?: AbortSignal) => {
    const update = async <T,>(path: string, apply: (value: T) => void, report = false) => {
      try {
        const value = await dataRequest<T>(path, undefined, signal ? AbortSignal.any([signal, AbortSignal.timeout(10000)]) : AbortSignal.timeout(10000));
        if (!signal?.aborted) { apply(value); if (report) setPollErrors((current) => ({ ...current, [path]: "" })); }
      } catch (error) {
        if (!signal?.aborted && report) setPollErrors((current) => ({ ...current, [path]: `状态更新失败，以下内容可能已过期：${errorMessage(error)}` }));
      }
    };
    await Promise.all([
      update<DataStatus>("status", (value) => { setStatus(value); setTaskList(value.tasks); }, true),
      update<FeedStatus>("feed/status", setFeed),
      update<Subscriptions>("subscriptions", (value) => setSubscriptions(value.items), true),
      update<DataInventory>("datasets?all_levels=true", setInventory, true),

    ]);
  }, []);

  useEffect(() => {
    if (taskList === null) return;
    const previous = seenTasks.current;
    seenTasks.current = new Map(tasks.map((job) => [job.id, job.status]));
    if (!previous) return;
    const changed = taskTransitions(previous, tasks);
    if (!changed.length) return;
    const completed = changed.filter((job) => job.status !== "running");
    if (completed.length) {
      setMessage(completed.map((job) => `${String(job.parameters.symbol ?? "")}：${dataJobLabels[job.action] ?? "数据操作"}${dataStateLabels[job.status] ?? "状态待确认"}${job.status === "succeeded" ? "。" : "，请检查数据状态；已完成的写入或删除不会回滚。"}`).join("；"));
      void refresh();
    }
  }, [tasks, taskList, refresh]);

  useEffect(() => {
    const controller = new AbortController();
    dataRequest<DataCapabilities>("capabilities", undefined, controller.signal).then(setCapabilities)
      .catch((error: unknown) => { if (!controller.signal.aborted) setMessage(errorMessage(error)); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number;
    async function poll() {
      await refresh(controller.signal);
      if (!controller.signal.aborted) timer = window.setTimeout(() => void poll(), 3000);
    }
    void poll();
    const clock = window.setInterval(() => setNow(Date.now()), 1000);
    return () => { controller.abort(); window.clearTimeout(timer); window.clearInterval(clock); };
  }, [refresh]);

  useEffect(() => {
    if (mode !== "configure" || !adding || !product) return;
    const controller = new AbortController();
    setMarketsLoading(true); setMarketError("");
    dataRequest<DataMarket[]>(`markets?product=${product}`, undefined, AbortSignal.any([controller.signal, AbortSignal.timeout(35000)]))
      .then((value) => { if (!controller.signal.aborted) setMarkets(value); })
      .catch((error: unknown) => { if (!controller.signal.aborted) { setMarkets([]); setMarketError(errorMessage(error)); } })
      .finally(() => { if (!controller.signal.aborted) setMarketsLoading(false); });
    return () => controller.abort();
  }, [mode, adding, marketRetry, product]);

  useEffect(() => {
    if (mode === "prepare" && (message || submissionStage)) {
      submissionFeedback.current?.scrollIntoView({ block: "nearest" });
    }
  }, [mode, message, submissionStage]);

  function open(action: DialogMode, item?: DataItem) {
    document.querySelectorAll<HTMLDetailsElement>(".data-row-menu[open]").forEach((menu) => {
      menu.open = false;
      menu.querySelector("summary")?.focus();
    });
    setMode(action); setAdding(!item && action === "configure"); setMessage(""); setMarketSearch(""); setProduct(item ? item.product ?? "futures_um" : ""); setOptionUnderlying(""); setOptionExpiry(""); setOptionRight(""); setMarkets([]); setMarketError(""); setMarketsLoading(false);
    setPlan(null); setDeletion(null); setConfigurationPlan(null); setConfirmation("");
    setSymbol(item?.symbol ?? "");
    setDataLevel(item?.level ?? "1m");
    const saved = subscriptions.find((value) => value.policy.symbol === item?.symbol && value.policy.product === (item?.product ?? "futures_um"))?.policy;
    setBaseLevel(saved?.base_level === "tick" ? "tick" : "1m");
    setRealtimeEnabled(saved?.realtime_enabled ?? (item?.product !== "options"));
    setHistoryEnabled(saved?.history_enabled ?? false);
    setHistoryStart(saved?.history_start?.slice(0, 10) ?? "");
    const latest = item?.event_ranges_ns.at(-1)?.[1];
    const first = latest ? eventDate(latest).slice(0, 10) : new Date(Date.now() - 86400000).toISOString().slice(0, 10);
    setStart(first); setEnd(new Date(new Date(`${first}T00:00:00Z`).getTime() + 86400000).toISOString().slice(0, 10));
  }

  async function act(operation: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await operation(); } catch (error) { setMessage(errorMessage(error)); }
    finally { setBusy(false); setSubmissionStage(""); }
  }

  async function checkPlan(parameters: DataSelection) {
    setSubmissionStage("正在检查所选范围…");
    try {
      const value = await dataRequest<DataPlan>("prepare/plan", parameters, AbortSignal.timeout(35000));
      setPlan({ key, value });
    } catch (error) {
      if (error instanceof DOMException && error.name === "TimeoutError") throw new Error("检查所选范围超时，请稍后重试。");
      throw error;
    }
  }

  async function createJob() {
    await act(async () => {
      const parameters = selection(symbol, start, end, dataLevel, currentProduct, true);
      await checkPlan(parameters);
      setSubmissionStage("正在创建数据任务…");
      let job: DataJob;
      try {
        job = await dataRequest<DataJob>("jobs", {
          ...parameters, action: "prepare",
        }, AbortSignal.timeout(35000));
      } catch (error) {
        if (error instanceof TypeError || error instanceof DOMException && ["TimeoutError", "AbortError"].includes(error.name)) {
          setMode(null);
          throw new Error("提交状态待确认，系统将自动刷新数据状态，请勿重复提交。");
        }
        throw error;
      }
      setTaskList((current) => [job, ...(current ?? []).filter((item) => item.id !== job.id)]);
      setPlan(null); setDeletion(null); setConfirmation("");
      setMode(null); setMessage("已提交，系统将自动处理并更新数据。");
      await refresh();
    });
  }

  async function saveConfiguration() {
    let proposal = currentConfiguration;
    if (!proposal) {
      proposal = await dataRequest<ConfigurationPlan>("subscriptions/plan", {
        symbol, product: currentProduct, base_level: baseLevel,
        intervals: currentProduct === "options" ? ["1m", "5m", "15m"] : [],
        realtime_enabled: realtimeEnabled, history_enabled: historyEnabled,
        history_mode: historyStart ? "start" : "all", history_start: historyStart ? `${historyStart}T00:00:00Z` : null,
      }, AbortSignal.timeout(35000));
      if (proposal.requires_confirmation) {
        setConfigurationPlan({ key: configurationKey, value: proposal }); setConfirmation("");
        await refresh();
        return;
      }
    }
    if (proposal.requires_confirmation && confirmation !== `DELETE ${symbol}`) throw new Error("请确认删除范围并输入确认文字。");
    try {
      const result = await dataRequest<{ policy: DataPolicy; job?: DataJob }>("subscriptions", {
        ...proposal.policy,
        ...(proposal.requires_confirmation ? { deletion_snapshot: proposal.deletion!.snapshot, confirmation } : {}),
      }, AbortSignal.timeout(35000));
      setConfigurationPlan(null); setConfirmation("");
      if (result.job) {
        const job = result.job;
        setTaskList((current) => [job, ...(current ?? []).filter((item) => item.id !== job.id)]);
        setMode(null); setMessage("正在删除旧数据，成功后将应用新配置并恢复服务。");
      } else {
        setMode(null); setMessage("交易对配置已保存。");
      }
      await refresh();
    } catch (error) {
      setConfigurationPlan(null); setConfirmation("");
      if (error instanceof TypeError || error instanceof DOMException && ["TimeoutError", "AbortError"].includes(error.name)) {
        setMode(null);
        throw new Error("配置提交状态待确认，系统将自动刷新数据状态，请勿重复提交。");
      }
      throw error;
    }
  }

  const scope = <p className="data-dialog-scope">{symbol || "尚未选择交易对"} <span>{mode === "configure" ? dataLevelLabels[baseLevel] ?? baseLevel : levelLabel}</span><small>Binance · {dataProductLabels[currentProduct]} · UTC</small></p>;
  const range = <div className="data-dialog-range">
    <label>开始日期（UTC）<input type="date" value={start} onChange={(event) => setStart(event.target.value)} /></label>
    <label>结束日期（不含）<input type="date" value={end} onChange={(event) => setEnd(event.target.value)} /></label>
    <small>{currentProduct === "options" && dataLevel === "tick" ? "获取近期接口实际返回的成交，允许包含当天。" : "历史获取仅限已完成的 UTC 日，可选择跨月或跨年的范围；数据按日处理。"}</small>
  </div>;

  return <div className="market-data-panel">
    <div className="data-overview-heading">
      <div className="data-overview-actions"><button type="button" className="tick-primary" disabled={busy || !capabilities?.can_operate} onClick={() => open("configure")}>＋ 添加交易对</button></div>
    </div>
    {pollError && <p role="alert" className="data-management-message">{pollError}</p>}
    {status?.monitor?.configuration_error && <p role="alert" className="data-management-message">配置同步失败：{status.monitor.configuration_error}</p>}
    {!mode && message && <p role="status" className="data-management-message">{message}</p>}
    <div className="data-overview-stats">
      <article><small>本地已有数据</small><b>{inventory ? grouped.filter(hasHistory).length : "—"}<span> 个交易对</span></b></article>
      <article><small>实时正在运行</small><b>{inventory ? grouped.filter((item) => liveStatus(item) === "运行中").length : "—"}<span> 个交易对</span></b></article>
      <article><small>存储状态</small><b>{status ? status.storage.available ? status.storage.writable ? "可读写" : "只读" : "不可用" : "读取中"}</b><span>{status?.storage.message ?? (status?.storage.free_bytes != null ? `剩余 ${displayBytes(status.storage.free_bytes)}` : "等待存储状态")}</span></article>
    </div>
    <div className="data-inventory-filters">
      <label>产品筛选<select value={filterProduct} onChange={(event) => { setFilterProduct(event.target.value); setFilterSymbol(""); }}><option value="">全部产品</option><option value="futures_um">U 本位永续</option><option value="options">期权</option></select></label>
      <label>交易对<select value={filterSymbol} onChange={(event) => setFilterSymbol(event.target.value)}><option value="">全部交易对</option>{symbols.map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>数据级别<select value={filterLevel} onChange={(event) => setFilterLevel(event.target.value)}><option value="">全部级别</option>{["tick", ...(capabilities?.intervals ?? [])].map((value) => <option key={value} value={value}>{dataLevelLabels[value] ?? value}</option>)}</select></label>
      <label>数据状态<select value={filterState} onChange={(event) => setFilterState(event.target.value)}><option value="">全部状态</option><option value="stored">已有历史数据</option><option value="empty">暂无历史数据</option><option value="live">实时运行中</option></select></label>
      <span>{inventory ? `${visible.length} 个交易对` : "正在读取…"}</span>
    </div>
    <div className="data-inventory-table tick-table-wrap"><table><thead><tr><th>数据</th><th>数据开始日期（UTC）</th><th>实时状态</th><th>最新数据 / 消息（UTC）</th><th>质量</th><th>操作</th></tr></thead><tbody>
      {visible.map((item) => {
        const live = liveStatus(item);
        const ranges = item.event_ranges_ns;
        const subscription = subscriptions.find((value) => value.policy.symbol === item.symbol && value.policy.product === (item.product ?? "futures_um"));
        const startDate = subscription?.policy.history_start?.slice(0, 10)
          ?? (ranges.length ? eventDate(ranges[0][0]).slice(0, 10) : null);
        const sourceFeed = subscription?.runtime ?? feed;
        const lastMessage = item.product !== "options" && (subscription || item.level === "1m") ? (sourceFeed?.symbols?.[item.symbol]?.last_update_ms ?? sourceFeed?.symbols?.[item.symbol]?.last_websocket_ms) : null;
        return <tr key={`${item.product ?? "futures_um"}-${item.symbol}-${item.level}`}><td><button type="button" className="data-item-name" onClick={() => open("configure", item)}>{item.symbol}<small>{dataProductLabels[item.product ?? "futures_um"]} · {dataLevelLabels[item.levels[0]] ?? item.levels[0]}</small></button></td>
          <td>{startDate ? <b>{startDate}</b> : <span className="data-muted">未设置</span>}</td>
          <td><span className={`data-state-badge${live === "运行中" ? " is-live" : ""}`}>{live}</span></td>
          <td><small>历史：{ranges.length ? eventDate(ranges.at(-1)![1]).replace(" UTC", "") : "—"}</small><small>实时更新：{lastMessage != null ? new Date(lastMessage).toISOString().replace("T", " ").replace("Z", "") : "—"}</small></td>
          <td><span className="data-muted">{dataQuality(item)}</span></td>
          <td><details className="data-row-menu" name="data-row-actions" onToggle={(event) => {
            if (event.currentTarget.open) event.currentTarget.querySelector("button:last-child")?.scrollIntoView({ block: "nearest" });
          }}><summary aria-label={`操作 ${item.symbol} ${dataLevelLabels[item.level] ?? item.level}`}>⋯</summary><div>
            <button type="button" onClick={() => open("configure", item)}>查看详情</button>
            {capabilities?.can_operate && <button type="button" className="data-danger-text" onClick={() => open("delete_pair", item)}>删除交易对</button>}
          </div></details></td></tr>;
      })}
    </tbody></table>{!inventory ? <p className="data-table-empty">正在读取数据现状…</p> : !visible.length && <p className="data-table-empty">{items.length ? "没有符合筛选条件的数据项。" : "暂无行情数据。点击“添加交易对”，从 Binance 选择交易对。"}</p>}</div>
    <p className="data-inventory-note">按产品和交易对统一管理。数据开始日期优先显示交易对配置；未配置起点时显示本地最早数据日期。保存方式和开始日期可在详情中调整。文件覆盖不等于连续或校验通过；实时更新按交易对配置运行，完整性以实际来源和缺口状态为准。</p>

    {mode && <DataDialog key={mode} title={mode === "configure" && !adding ? "交易对详情" : dialogTitles[mode]} busy={busy} onClose={() => setMode(null)}>
      {busy && <p role="status">{submissionStage || "正在处理…"}</p>}
      {mode !== "prepare" && message && <p role="status" className="data-management-message">{message}</p>}
      {pollError && <p role="alert" className="data-management-message">{pollError}</p>}
      {mode !== "configure" && <>
        {pairBusy && <p>该交易对已有任务运行或状态待确认，系统将自动更新状态，请勿重复提交；其他交易对仍可操作。</p>}
        {status && (!status.storage.available || !status.storage.writable) && <p>{status.storage.message || "数据存储当前不可写。"}</p>}
      </>}
      {scope}
      {mode === "prepare" && !adding && <label>查看 / 操作的数据级别<select value={dataLevel} onChange={(event) => setDataLevel(event.target.value)}>{levels.map((value) => <option key={value} value={value}>{dataLevelLabels[value] ?? value}</option>)}</select></label>}
      {mode === "delete_pair" && <div className="tick-delete-body">
        <p className="data-danger-text">删除 {symbol} 的交易对配置及全部日期、全部级别的行情数据，包含原始归档、指标缓存和托管实时数据。操作不可撤销。</p>
        <p>预览会先停止该交易对的实时采集，并关闭历史追加。取消弹窗后不会自动恢复采集。请先停止手动采集、Pipeline 和相关回测。</p>
        <button type="button" disabled={disabled || !symbol} onClick={() => void act(async () => {
          setDeletion(null); setConfirmation("");
          try {
            setDeletion({ key: deletionKey, value: await dataRequest<DataDeletionPlan>("pairs/delete/plan", { symbol, product: currentProduct } satisfies PairDeletePlanRequest) });
          } finally { await refresh(); }
        })}>停止采集并预览删除</button>
        {currentDeletion && <div className="tick-delete-confirm">
          <p>{currentDeletion.file_count} 个文件 · {displayBytes(currentDeletion.total_bytes)}</p><p>{currentDeletion.message}</p>
          <details><summary>文件清单</summary><ul>{currentDeletion.files.map((file, index) => <li key={index}>{String(file.root)}/{String(file.path)}</li>)}</ul></details>
          <label>输入 DELETE {symbol} 确认删除交易对<input type="text" autoComplete="off" spellCheck={false} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label>
          <button type="button" className="data-danger-button" disabled={disabled || confirmation !== `DELETE ${symbol}`} onClick={() => void act(async () => {
            const job = await dataRequest<DataJob>("pairs/delete", { symbol, product: currentProduct, deletion_snapshot: currentDeletion.snapshot, confirmation } satisfies PairDeleteRequest, AbortSignal.timeout(35000));
            setTaskList((current) => [job, ...(current ?? []).filter((item) => item.id !== job.id)]);
            setDeletion(null); setConfirmation(""); setMode(null); setMessage("已提交删除，系统将自动更新数据状态。");
            await refresh();
          })}>确认删除交易对及全部行情</button>
        </div>}
      </div>}
      {mode === "configure" && pairBusy && <p role="status">该交易对正在处理数据，完成后可继续调整配置。</p>}
      {mode === "configure" && <fieldset disabled={busy || !capabilities?.can_operate || pairBusy} className="data-configuration-fields">
        {adding && <>
          <div className="data-market-picker"><label>产品<select value={product} onChange={(event) => { setProduct(event.target.value); setSymbol(""); setMarkets([]); setMarketSearch(""); setMarketError(""); setMarketsLoading(false); setOptionUnderlying(""); setOptionExpiry(""); setOptionRight(""); setDataLevel("1m"); setBaseLevel("1m"); setHistoryEnabled(false); setRealtimeEnabled(event.target.value !== "options"); }}><option value="">请选择产品</option><option value="futures_um">U 本位永续（USDT / USDC）</option><option value="options">期权</option></select></label></div>
          {product && <div className="data-market-picker">
            {product === "options" && <>
              <label>标的交易对<select value={optionUnderlying} disabled={marketsLoading} onChange={(event) => { setOptionUnderlying(event.target.value); setOptionExpiry(""); setSymbol(""); setMarketSearch(""); }}><option value="">请选择标的交易对</option>{underlyings.map((value) => <option key={value}>{value}</option>)}</select></label>
              <div className="data-dialog-range"><label>到期日<select value={optionExpiry} onChange={(event) => { setOptionExpiry(event.target.value); setSymbol(""); }}><option value="">全部到期日</option>{expiries.map((value) => <option key={value} value={String(value)}>{new Date(value).toISOString().slice(0, 10)} · {new Date(value).toISOString().slice(11, 16)} UTC</option>)}</select></label><label>期权方向<select value={optionRight} onChange={(event) => { setOptionRight(event.target.value); setSymbol(""); }}><option value="">全部方向</option><option value="CALL">看涨 CALL</option><option value="PUT">看跌 PUT</option></select></label></div>
            </>}
            <label>{product === "options" ? "搜索合约 / 行权价" : "搜索 Binance 交易对"}<input type="search" value={marketSearch} placeholder={product === "options" ? "输入合约代码或行权价" : "输入币种，例如 XRP"} onChange={(event) => { setMarketSearch(event.target.value); setSymbol(""); }} /></label>
            <label>{product === "options" ? "期权合约" : "交易对"}<select size={6} value={symbol} disabled={marketsLoading || Boolean(marketError) || (product === "options" && !optionUnderlying)} onChange={(event) => setSymbol(event.target.value)}><option value="">请选择{product === "options" ? "具体期权合约" : "交易对"}</option>{marketOptions.map((item) => <option key={item.symbol} value={item.symbol}>{product === "options" ? `${item.symbol} · ${item.right === "CALL" ? "看涨" : "看跌"} · 单位 ${item.multiplier}` : `${item.base} / ${item.quote} · ${item.symbol}`}</option>)}</select></label>
            <small>{marketsLoading ? "正在读取 Binance 品种…" : marketError ? "品种读取失败" : `${marketOptions.length} 个匹配合约 · Binance ${dataProductLabels[currentProduct]}`}</small>{marketError && <p role="alert">{marketError} <button type="button" onClick={() => setMarketRetry((value) => value + 1)}>重新加载</button></p>}
          </div>}
        </>}
        {currentProduct === "futures_um" ? <>
          <label>保存数据<select value={baseLevel} onChange={(event) => setBaseLevel(event.target.value)}><option value="1m">全部周期 K 线</option><option value="tick">Tick 与全部周期 K 线</option></select></label>
          <p>自动生成 Binance 全部周期：{levels.filter((value) => value !== "tick" && value !== "1s").join("、")}。周期按 UTC 对齐，未闭合与不完整数据分别标记。</p>
        </> : <><label>保存数据<select value={baseLevel} onChange={(event) => setBaseLevel(event.target.value)}><option value="1m">K 线</option><option value="tick">近期 Tick</option></select></label><p>期权使用现有支持的周期：1m、5m、15m。</p></>}
        {currentProduct === "futures_um" && <>
          <label>数据开始日期（UTC 00:00）<input type="date" value={historyStart} max={shiftDay(new Date(now).toISOString().slice(0, 10), -1)} onChange={(event) => setHistoryStart(event.target.value)} /></label>
          <small>留空且启用自动补齐时使用交易所最早可用日期。服务启用时，调早后自动补齐；调晚会先暂停服务并预览删除范围，确认后删除此前行情、缓存及原始归档。</small>
          <label><input type="checkbox" checked={historyEnabled} onChange={(event) => setHistoryEnabled(event.target.checked)} />自动补齐历史数据</label>
        </>}
        <label><input type="checkbox" checked={realtimeEnabled} disabled={currentProduct !== "futures_um"} onChange={(event) => setRealtimeEnabled(event.target.checked)} />实时加载并自动更新</label>
        <p>{currentProduct === "futures_um" ? "默认持续更新，服务重启恢复采集。未闭合 K 线单独展示；发现缺口会保留补缺位置。Tick 使用真实逐笔来源，缺口未补齐时标记为不完整。" : "期权目前支持保存配置及已有历史获取，连续采集和自动历史追加尚未接入。"}</p>
        <p>新数据按所选保存方式写入，K 线自动生成全部官方周期。开始日期调晚时，确认后删除旧数据。数据状态不代表策略获准交易。</p>
        {currentConfiguration?.requires_confirmation && currentConfiguration.deletion && <div className="tick-delete-confirm">
          <p className="data-danger-text">开始日期将调整为 {currentConfiguration.policy.history_start?.slice(0, 10)} UTC 00:00，将删除此前数据和原始归档。</p>
          <p>该交易对服务已暂停；取消不会自动恢复。删除成功后才应用新配置并恢复所选服务。</p>
          <p>{currentConfiguration.deletion.file_count} 个文件需删除或裁剪 · 当前大小 {displayBytes(currentConfiguration.deletion.total_bytes)}</p>
          <p>{currentConfiguration.deletion.message}</p>
          <details><summary>受影响文件</summary><ul>{currentConfiguration.deletion.files.map((file, index) => <li key={index}>{String(file.root)}/{String(file.path)} · {file.operation === "delete" ? "删除" : "裁剪旧记录"}</li>)}</ul></details>
          {!!currentConfiguration.deletion.retained_files?.length && <details><summary>保留的跨界或日期不明确文件</summary><ul>{currentConfiguration.deletion.retained_files.map((file) => <li key={file.path}>{file.path} · {file.reason}</li>)}</ul></details>}
          <label>输入 DELETE {symbol} 确认<input type="text" autoComplete="off" spellCheck={false} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label>
        </div>}
        {currentProduct === "options" && !adding && <button type="button" disabled={busy} onClick={() => open("prepare", selectedItem)}>获取官方数据</button>}
        <button type="button" className={currentConfiguration?.requires_confirmation ? "data-danger-button" : "tick-primary"}
          disabled={busy || !capabilities?.can_operate || !symbol || ((realtimeEnabled || historyEnabled) && !canWrite)
            || (adding && (!product || marketsLoading || Boolean(marketError)))
            || Boolean(currentConfiguration?.requires_confirmation && confirmation !== `DELETE ${symbol}`)}
          onClick={() => void act(saveConfiguration)}>{busy ? "正在保存…" : currentConfiguration?.requires_confirmation ? "确认删除旧数据并保存配置" : adding ? "添加交易对" : "保存配置"}</button>
      </fieldset>}
      {mode === "prepare" && <>
        {!(currentProduct === "options" && dataLevel === "tick") && range}
        <p>{currentProduct === "options" ? dataLevel === "tick" ? "获取官方近期接口返回的最多 500 笔成交，实际日期以记录为准。仅保存可观察片段，不代表完整历史 Tick，也不启动持续采集。" : "直接获取交易所历史 K 线，按完整 UTC 日处理。不会从 K 线反推 Tick，也不证明底层逐笔数据完整。" : "只补缺失，不强制重建。选择 K 线时，会保留底层 Tick 并生成所选周期。"}</p>
        {currentPlan && <div className="data-operation-plan">{currentProduct === "options" ? <p>{currentPlan.message}</p> : <><p>所选 {currentPlan.archive_days} 天 · {currentPlan.missing_days} 天待补齐或检查</p><details><summary>查看每日覆盖</summary><ul>{currentPlan.days.map((day) => <li key={day.day}>{day.day} · {day.catalog_present ? "所选级别文件存在" : "待补齐或检查"}</li>)}</ul></details></>}</div>}
        <div className="data-dialog-actions">
          <button type="button" disabled={busy || !canWrite || !symbol || marketsLoading || Boolean(adding && marketError)} onClick={() => void act(() => checkPlan(selection(symbol, start, end, dataLevel, currentProduct, true)))}>检查所选范围</button>
          <button type="button" className="tick-primary" disabled={disabled || !symbol || marketsLoading || Boolean(adding && (!product || marketError))} onClick={() => void createJob()}>{busy ? "处理中…" : adding ? "添加数据" : currentProduct === "options" && dataLevel === "tick" ? "获取近期成交" : "获取数据"}</button>
        </div>
        <div ref={submissionFeedback} id="data-prepare-feedback">
          {message && <p role="alert" className="data-management-message">{message}</p>}
        </div>
      </>}
    </DataDialog>}
  </div>;
}

function DataDialog({ title, busy, onClose, children }: { title: string; busy: boolean; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const dialog = ref.current!;
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  function close() {
    if (busy) return;
    ref.current?.close();
    onClose();
    previousFocus.current?.focus();
  }
  return <dialog ref={ref} className="data-dialog" aria-labelledby="data-dialog-title" onCancel={(event) => { event.preventDefault(); close(); }}>
    <div className="data-dialog-inner"><header><h2 id="data-dialog-title">{title}</h2><button type="button" disabled={busy} aria-label="关闭弹窗" onClick={close}>×</button></header>{children}</div>
  </dialog>;
}

