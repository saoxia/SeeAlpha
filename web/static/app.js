"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
  const finite = (value) => typeof value === "number" && Number.isFinite(value);
  const number = (value) => finite(value) ? value.toLocaleString("zh-CN") : "—";
  const percent = (value) => finite(value) ? `${(value * 100).toFixed(1)}%` : "—";
  const dateFormat = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
  const date = (value) => {
    if (value === null || value === undefined || value === "") return "—";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? "—" : dateFormat.format(parsed);
  };
  const duration = (value) => {
    if (!finite(value)) return "—";
    if (value < 60) return `${Math.round(value)} 秒`;
    if (value < 3600) return `${(value / 60).toFixed(1)} 分钟`;
    if (value < 86400) return `${(value / 3600).toFixed(1)} 小时`;
    return `${(value / 86400).toFixed(1)} 天`;
  };
  const bytes = (value) => finite(value) ? `${(value / (1024 ** 3)).toFixed(1)} GB` : "—";
  const list = (value) => Array.isArray(value) ? value : [];
  const icons = {
    pulse: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    chart: '<path d="M4 20V4m0 16h16M8 15l4-5 4 3 5-8"/>',
    shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z"/><path d="m8 12 3 3 5-6"/>',
    layers: '<path d="m12 3 10 5-10 5L2 8zm-10 9 10 5 10-5M2 16l10 5 10-5"/>',
    terminal: '<rect x="2" y="3" width="20" height="18" rx="3"/><path d="m6 8 4 4-4 4m8 0h4"/>',
    check: '<path d="m5 12 4 4L19 6"/><circle cx="12" cy="12" r="9"/>',
    database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
    file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6M8 13h8m-8 4h5"/>',
  };
  const icon = (key, className = "") => `<svg class="${className}" viewBox="0 0 24 24" aria-hidden="true">${icons[key] || icons.file}</svg>`;
  const badge = (label, tone = "") => `<span class="badge ${tone}">${escapeHTML(label)}</span>`;
  const empty = (text, detail = "") => `<div class="empty-state">${icon("layers", "empty-icon")}${escapeHTML(text)}${detail ? `<br><span>${escapeHTML(detail)}</span>` : ""}</div>`;
  const statusLabels = { running: "运行中", succeeded: "已完成", failed: "失败", cancelled: "已停止", interrupted: "已中断", skipped: "已跳过" };
  const statusTones = { running: "running", succeeded: "ok", failed: "bad", cancelled: "warn", interrupted: "warn", skipped: "" };
  const jobBadge = (job) => badge(statusLabels[job.status] || "未知状态", statusTones[job.status] || "");
  const pageMeta = {
    overview: ["运行总览", "WORKSPACE OVERVIEW", "查看项目检查、策略回测和研究任务。"],
    research: ["策略与回测", "STRATEGY RESEARCH", "从一个想法开始，用已有策略与历史数据验证研究。"],
    reports: ["报告中心", "REPORT LIBRARY", "汇集回测结果，随时回看策略表现与交易细节。"],
    jobs: ["任务与日志", "TASK ACTIVITY", "跟踪任务进度，查看执行输出与完成结果。"],
  };
  const actionMeta = {
    check_quick: ["shield", "源码与代码质量检查"],
    check_full: ["check", "完整检查与项目测试"],
  };
  let state = null;
  let connected = false;
  let page = "overview";
  let pending = false;
  let selectedJob = null;
  let jobDetail = null;
  let stateRequest = 0;
  let stateLoading = false;
  let logLoading = false;
  let strategySignature = "";
  let reportLimit = 100;
  let lastLogText = null;
  let logError = false;

  async function api(path, options = {}) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(path, { cache: "no-store", credentials: "same-origin", ...options, signal: controller.signal });
      let result;
      try { result = await response.json(); }
      catch (_) { throw new Error(`服务返回了无法读取的响应（HTTP ${response.status}）`); }
      if (!response.ok) throw new Error(result.error || `请求失败（HTTP ${response.status}）`);
      return result;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("请求超时，请刷新状态后重试。");
      if (error instanceof TypeError) throw new Error("无法连接管理服务，请检查服务是否仍在运行。");
      throw error;
    } finally { window.clearTimeout(timeout); }
  }

  function notify(text, tone = "") {
    const node = $("action-message");
    node.textContent = text;
    node.className = `banner ${tone}`;
    node.hidden = false;
  }

  const snapshot = (key) => state?.[key] || { data: null, stale: false, error: null, age_seconds: null };
  const snapshotTime = (item) => date(item.data?.checked_at ?? item.data?.checked_ms ?? item.data?.created_at);
  const freshness = (item) => !item.data ? badge("暂无快照") : item.stale ? badge("快照已过期", "warn") : badge("已有快照", "ok");
  const runningJob = () => list(state?.jobs).find((job) => job.status === "running");
  const reportURL = (value) => {
    if (typeof value !== "string" || !value) return null;
    try {
      const url = new URL(value, window.location.origin);
      if (url.origin !== window.location.origin || !["http:", "https:"].includes(url.protocol) || url.pathname.startsWith("/api/")) return null;
      return `${url.pathname}${url.search}${url.hash}`;
    } catch (_) { return null; }
  };
  const reportLink = (url, label, className = "text-link") => {
    const safe = reportURL(url);
    return safe ? `<a href="${escapeHTML(safe)}" target="_blank" rel="noopener noreferrer" class="${className}">${escapeHTML(label)}</a>` : "";
  };

  async function refreshState(force = false) {
    if (stateLoading && !force) return;
    const request = ++stateRequest;
    stateLoading = true;
    $("refresh-button").disabled = true;
    try {
      const result = await api("/api/state");
      if (request !== stateRequest) return;
      state = result;
      connected = true;
      $("connection-banner").hidden = true;
      $("last-refreshed").textContent = `页面同步：${date(result.server_time || Date.now())} · 每 10 秒刷新`;
      renderState();
    } catch (error) {
      if (request !== stateRequest) return;
      connected = false;
      const bannerNode = $("connection-banner");
      bannerNode.className = "banner error";
      bannerNode.textContent = `${error.message}${state ? " 当前保留上次读取的状态，不能代表实时情况。" : ""} 将自动重试。`;
      bannerNode.hidden = false;
      renderAvailability();
    } finally {
      if (request === stateRequest) {
        stateLoading = false;
        $("refresh-button").disabled = false;
      }
    }
  }

  function metric(label, value, detail, iconName, textValue = false) {
    return `<article class="metric-card"><div class="metric-head"><span>${escapeHTML(label)}</span><span class="metric-icon">${icon(iconName)}</span></div><div class="metric-value${textValue ? " text-value" : ""}">${value}</div><div class="metric-foot">${detail}</div></article>`;
  }

  function renderOverview() {
    const checks = snapshot("checks");
    const countReports = finite(state.report_count) ? state.report_count : list(state.reports).length;
    const running = runningJob();
    const checkState = !checks.data ? "尚无检查记录" : checks.stale ? "检查记录已过期" : checks.data.passed ? "检查通过" : "检查未通过";
    const checksInfo = checks.data ? `${checks.data.full_tests ? "完整检查" : "快速检查"} · ${list(checks.data.steps).filter((step) => step.passed === true).length} / ${list(checks.data.steps).length} 项通过` : checks.error || "发起项目检查后查看结果";
    $("overview-metrics").innerHTML =
      metric("项目检查", escapeHTML(checkState), escapeHTML(checksInfo), "check", true) +
      metric("可用策略", `${number(list(state.strategies).length)}<span class="unit">个</span>`, "选择策略并使用本地历史数据回测", "layers") +
      metric("研究报告", `${number(countReports)}<span class="unit">份</span>`, "查看历史研究结果", "file") +
      metric("当前任务", escapeHTML(running ? running.label || "任务运行中" : "暂无运行任务"), `${running ? badge("运行中", "running") : badge("空闲")}${escapeHTML(running ? "可在任务页查看实时日志" : `${list(state.jobs).length} 条任务记录`)}`, "terminal", true);
    $("snapshot-list").innerHTML = snapshotRow("项目检查", "check", checks, checksInfo, checks.data?.passed, checks.data?.passed ? "检查通过" : "检查未通过");
    const jobs = list(state.jobs).slice(0, 4);
    $("recent-jobs").innerHTML = jobs.length ? jobs.map((job) => `<button type="button" class="recent-job" data-job="${escapeHTML(job.id)}"><div><div class="recent-title">${escapeHTML(job.label || job.action)}</div><div class="recent-time">${escapeHTML(date(job.created_at))}</div></div>${jobBadge(job)}</button>`).join("") : empty("还没有任务记录", "从日常运维发起第一次检查。");
  }

  function snapshotRow(label, iconName, item, info, passed, outcome) {
    const status = !item.data ? badge("暂无记录") : item.stale ? badge("已过期", "warn") : typeof passed !== "boolean" ? badge("已记录") : badge(outcome, passed ? "ok" : "warn");
    return `<div class="snapshot-row"><div><div class="snapshot-label">${icon(iconName)}${escapeHTML(label)}</div><div class="snapshot-info">${escapeHTML(info)}<br>快照：${escapeHTML(snapshotTime(item))}</div></div>${status}</div>`;
  }

  function renderActions() {
    const actions = list(state.actions).filter((action) => actionMeta[action.id]);
    $("operations-actions").innerHTML = actions.length ? actions.map((action) => `<button type="button" class="operation-button" data-action="${escapeHTML(action.id)}" title="${escapeHTML(action.description || "")}">${icon(actionMeta[action.id][0])}<strong>${escapeHTML(action.label)}</strong><span>${escapeHTML(actionMeta[action.id][1])}</span></button>`).join("") : empty("暂无可用操作");
  }

  function renderStrategies() {
    const strategies = list(state.strategies);
    const signature = JSON.stringify(strategies);
    if (signature !== strategySignature) {
      const previous = $("backtest-strategy").value;
      const select = $("backtest-strategy");
      select.replaceChildren();
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "请选择策略";
      select.append(placeholder);
      for (const strategy of strategies) {
        const option = document.createElement("option");
        option.value = strategy.id;
        option.textContent = `${strategy.label}${strategy.symbol ? ` · ${strategy.symbol}` : ""}`;
        select.append(option);
      }
      select.value = strategies.some((strategy) => strategy.id === previous) ? previous : "";
      strategySignature = signature;
    }
    const query = $("strategy-search").value.trim().toLowerCase();
    const filtered = strategies.filter((strategy) => `${strategy.label} ${strategy.id} ${strategy.symbol || ""} ${strategy.description || ""}`.toLowerCase().includes(query));
    $("strategy-count").textContent = number(strategies.length);
    $("strategy-list").innerHTML = filtered.length ? filtered.map((strategy) => `<button type="button" class="strategy-card${$("backtest-strategy").value === strategy.id ? " selected" : ""}" data-strategy="${escapeHTML(strategy.id)}" aria-pressed="${$("backtest-strategy").value === strategy.id}"><div class="strategy-card-top"><strong class="strategy-name">${escapeHTML(strategy.label)}</strong>${strategy.symbol ? badge(strategy.symbol) : ""}</div><p>${escapeHTML(strategy.description || "使用项目中已有的策略配置进行回测。")}</p><span class="strategy-id">${escapeHTML(strategy.id)}</span></button>`).join("") : empty(query ? "没有匹配的策略" : "暂无可用策略", query ? "试试其他关键词。" : "服务尚未提供可回测策略。");
    updateStrategyDescription();
  }

  function updateStrategyDescription() {
    const selected = list(state?.strategies).find((strategy) => strategy.id === $("backtest-strategy").value);
    $("strategy-description").textContent = selected?.description || (selected ? "使用当前策略的既有配置进行回测。" : "从左侧策略库中选择一个策略。");
  }

  function renderReports() {
    const reports = list(state.reports);
    const query = $("report-search").value.trim().toLowerCase();
    const filtered = reports.filter((report) => `${report.name || ""} ${report.strategy || ""} ${report.run_id || ""}`.toLowerCase().includes(query));
    const total = finite(state.report_count) ? state.report_count : reports.length;
    $("nav-report-count").textContent = number(total);
    $("report-total").textContent = number(total);
    $("report-result-count").textContent = query ? `在已载入报告中找到 ${number(filtered.length)} 份结果` : `${number(total)} 份报告${reports.length < total ? ` · 已载入最近 ${number(reports.length)} 份` : ""} · 按更新时间排列`;
    const shown = filtered.slice(0, reportLimit);
    $("reports-table").innerHTML = shown.length ? `<table><thead><tr><th>报告 / 运行编号</th><th>策略</th><th>更新时间（北京）</th><th>查看结果</th></tr></thead><tbody>${shown.map((report) => `<tr><td><div class="report-name" title="${escapeHTML(report.name)}">${escapeHTML(report.name || "回测报告")}</div><div class="report-run" title="${escapeHTML(report.run_id)}">${escapeHTML(report.run_id || "—")}</div></td><td><div class="report-strategy" title="${escapeHTML(report.strategy)}">${escapeHTML(report.strategy || "—")}</div></td><td>${escapeHTML(date(report.modified_at))}</td><td><div class="report-links">${reportLink(report.url, "打开报告 ↗", "button")}${reportLink(report.summary_url, "摘要")}</div></td></tr>`).join("")}</tbody></table>${filtered.length > shown.length ? `<div class="load-more"><button type="button" class="button secondary" id="more-reports">继续显示报告 · 还有 ${number(filtered.length - shown.length)} 份</button></div>` : ""}` : empty(query ? "没有找到相关报告" : "还没有可用报告", query ? "尝试搜索策略名称或运行编号。" : "完成一次回测后，可在这里打开结果。");
  }

  function renderJobs() {
    const jobs = list(state.jobs);
    $("jobs-count").textContent = `${number(jobs.length)} 条记录${runningJob() ? " · 1 个任务运行中" : " · 当前无运行任务"}`;
    $("nav-running").hidden = !runningJob();
    $("jobs-list").innerHTML = jobs.length ? jobs.map((job) => `<button type="button" class="job-item${selectedJob === job.id ? " selected" : ""}" data-job="${escapeHTML(job.id)}" aria-pressed="${selectedJob === job.id}"><div class="job-item-head"><strong>${escapeHTML(job.label || job.action)}</strong>${jobBadge(job)}</div><p>${escapeHTML(date(job.created_at))}</p><p class="job-id">${escapeHTML(job.id)}</p></button>`).join("") : empty("暂无任务记录", "从运行总览发起检查，或创建一次回测。");
    if (!selectedJob && page === "jobs" && jobs.length) selectJob(jobs[0].id);
    else if (selectedJob) {
      const current = jobs.find((job) => job.id === selectedJob);
      if (current) renderJobDetail({ ...jobDetail, ...current }, false);
    }
  }

  function renderJobDetail(job, withLog = true) {
    if (!job || job.id !== selectedJob) return;
    $("log-title").textContent = job.label || job.action || "任务日志";
    $("log-description").textContent = `任务编号：${job.id}`;
    $("job-details").hidden = false;
    const parameters = job.parameters || {};
    const detail = Object.entries(parameters).map(([key, value]) => `${({ strategy: "策略", start: "开始", end: "结束", interval: "周期" })[key] || key}：${String(value)}`).join(" · ");
    $("job-details").innerHTML = `<span>开始<strong>${escapeHTML(date(job.created_at))}</strong></span><span>结束<strong>${escapeHTML(date(job.finished_at))}</strong></span><span>退出码<strong>${escapeHTML(job.returncode ?? "—")}</strong></span>${detail ? `<span class="job-parameters">${escapeHTML(detail)}</span>` : ""}`;
    $("log-state").textContent = statusLabels[job.status] || "未知状态";
    $("log-dot").className = `status-dot ${job.status === "running" ? "active" : "neutral"}`;
    $("cancel-job").hidden = job.status !== "running";
    $("cancel-job").disabled = pending || !connected;
    if (withLog) {
      const log = String(job.log ?? "");
      const text = log || job.error || (job.status === "running" ? "任务已启动，等待输出…" : "这个任务没有日志输出。");
      const output = $("job-log");
      if (text !== lastLogText) {
        const nearBottom = output.scrollHeight - output.scrollTop - output.clientHeight < 50;
        output.textContent = text;
        if (nearBottom || lastLogText === null) output.scrollTop = output.scrollHeight;
        lastLogText = text;
      }
      $("log-updated").textContent = `更新于 ${date(Date.now()).split(" ").pop()}`;
      const links = list(job.report_urls).map((url, index) => reportLink(url, `查看报告 ${index + 1} ↗`)).filter(Boolean);
      $("job-reports").hidden = !links.length;
      $("job-reports").innerHTML = links.length ? `<span>任务产出</span>${links.join("")}` : "";
      if (job.error && log) {
        const errorText = String(job.error);
        $("log-description").textContent = `任务编号：${job.id} · ${errorText}`;
      }
    }
  }

  async function refreshLog() {
    if (!selectedJob || page !== "jobs" || logLoading) return;
    const id = selectedJob;
    logLoading = true;
    try {
      const result = await api(`/api/jobs/${encodeURIComponent(id)}`);
      if (selectedJob !== id) return;
      jobDetail = result.job || result;
      logError = false;
      const previous = list(state?.jobs).find((job) => job.id === id);
      if (previous && previous.status !== jobDetail.status) refreshState();
      renderJobDetail(jobDetail);
    } catch (error) {
      if (selectedJob !== id) return;
      logError = true;
      $("log-updated").textContent = "日志连接异常 · 将重试";
      if (lastLogText === null) $("job-log").textContent = error.message;
    } finally {
      logLoading = false;
      if (selectedJob !== id && page === "jobs") refreshLog();
    }
  }

  function selectJob(id) {
    if (selectedJob !== id) {
      selectedJob = id;
      jobDetail = null;
      lastLogText = null;
      logError = false;
      $("job-log").textContent = "正在读取任务日志…";
      $("job-reports").hidden = true;
      $("job-details").hidden = true;
      $("log-updated").textContent = "正在读取…";
    }
    const job = list(state?.jobs).find((item) => item.id === id);
    if (job) renderJobDetail(job, false);
    if (page !== "jobs") navigate("jobs");
    else {
      document.querySelectorAll(".job-item").forEach((node) => {
        node.classList.toggle("selected", node.dataset.job === id);
        node.setAttribute("aria-pressed", String(node.dataset.job === id));
      });
      refreshLog();
    }
  }

  function renderAvailability() {
    const busy = Boolean(runningJob());
    const disabled = !connected || pending || busy || !state?.csrf_token;
    document.querySelectorAll("[data-action]").forEach((button) => { button.disabled = disabled; });
    $("backtest-submit").disabled = disabled || !$("backtest-strategy").value;
    $("backtest-availability").textContent = !connected ? "等待连接管理服务" : pending ? "正在提交，请稍候…" : busy ? "已有任务运行中，完成后可发起新的回测。" : !$("backtest-strategy").value ? "请选择一个回测策略" : "回测进度与结果将在任务页中显示。";
    $("cancel-job").disabled = pending || !connected;
  }

  function renderState() {
    renderOverview();
    renderActions();
    renderStrategies();
    renderReports();
    renderJobs();
    renderAvailability();
  }

  async function submitJob(action, parameters = {}) {
    if (!connected || pending || runningJob() || !state?.csrf_token) return;
    pending = true;
    renderAvailability();
    try {
      const result = await api("/api/jobs", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf_token },
        body: JSON.stringify({ action, parameters }),
      });
      const job = result.job;
      if (!job?.id) throw new Error("服务没有返回任务编号，请刷新任务列表确认是否已创建。");
      state.jobs = [job, ...list(state.jobs).filter((item) => item.id !== job.id)];
      notify(`已创建任务：${job.label || job.action}。可以在任务与日志页查看执行过程。`);
      selectJob(job.id);
      await refreshState(true);
    } catch (error) {
      notify(error.message, "error");
      await refreshState(true);
    } finally {
      pending = false;
      renderAvailability();
    }
  }

  async function cancelJob() {
    if (!selectedJob || pending || !connected || !state?.csrf_token) return;
    const id = selectedJob;
    pending = true;
    renderAvailability();
    try {
      const result = await api(`/api/jobs/${encodeURIComponent(id)}/cancel`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf_token }, body: "{}",
      });
      const returned = result.job;
      notify(returned?.status === "cancelled" ? "任务已停止，已产生的输出会保留。" : "停止请求已提交，请等待任务状态更新。", "warning");
      if (returned && selectedJob === id) renderJobDetail(returned, false);
      await refreshState(true);
      await refreshLog();
    } catch (error) {
      notify(error.message, "error");
      await refreshState(true);
    } finally {
      pending = false;
      renderAvailability();
    }
  }

  function navigate(next, updateHash = true) {
    page = pageMeta[next] ? next : "overview";
    const [title, eyebrow, description] = pageMeta[page];
    $("page-title").textContent = title;
    $("breadcrumb-page").textContent = title;
    $("page-eyebrow").textContent = eyebrow;
    $("page-description").textContent = description;
    document.title = `${title} · SeeAlpha 管理台`;
    document.querySelectorAll(".page").forEach((node) => { node.hidden = node.id !== `page-${page}`; });
    document.querySelectorAll("[data-page]").forEach((node) => {
      node.classList.toggle("active", node.dataset.page === page);
      node.setAttribute("aria-label", pageMeta[node.dataset.page][0]);
      if (node.dataset.page === page) node.setAttribute("aria-current", "page");
      else node.removeAttribute("aria-current");
    });
    if (updateHash && window.location.hash !== `#${page}`) history.replaceState(null, "", `#${page}`);
    if (page === "jobs") {
      if (state) renderJobs();
      refreshLog();
    }
  }

  $("refresh-button").addEventListener("click", () => { refreshState(true); if (page === "jobs") refreshLog(); });
  $("strategy-search").addEventListener("input", () => { if (state) renderStrategies(); });
  $("report-search").addEventListener("input", () => { reportLimit = 100; if (state) renderReports(); });
  $("backtest-strategy").addEventListener("change", () => { if (state) renderStrategies(); renderAvailability(); });
  $("cancel-job").addEventListener("click", cancelJob);
  $("backtest-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const strategy = $("backtest-strategy").value;
    const start = $("backtest-start").value;
    const end = $("backtest-end").value;
    const startTime = Date.parse(`${start}T00:00:00Z`);
    const endTime = Date.parse(`${end}T00:00:00Z`);
    const validation = $("backtest-validation");
    const error = !strategy ? "请选择策略。" : !Number.isFinite(startTime) || !Number.isFinite(endTime) ? "请填写有效的开始与结束日期。" : endTime <= startTime ? "结束日期必须晚于开始日期。" : endTime - startTime > 366 * 86400000 ? "单次回测区间不能超过 366 天。" : "";
    validation.textContent = error;
    validation.hidden = !error;
    if (!error) submitJob("backtest", { strategy, start, end, interval: $("backtest-interval").value });
  });
  document.addEventListener("click", (event) => {
    const node = event.target.closest("[data-page], [data-action], [data-job], [data-strategy], #more-reports");
    if (!node || node.disabled) return;
    if (node.dataset.page) { event.preventDefault(); navigate(node.dataset.page); }
    else if (node.dataset.action) submitJob(node.dataset.action);
    else if (node.dataset.job) selectJob(node.dataset.job);
    else if (node.dataset.strategy) {
      $("backtest-strategy").value = node.dataset.strategy;
      renderStrategies();
      renderAvailability();
      $("backtest-validation").hidden = true;
    } else if (node.id === "more-reports") { reportLimit += 100; renderReports(); }
  });
  window.addEventListener("hashchange", () => navigate(window.location.hash.slice(1), false));
  window.addEventListener("online", () => refreshState(true));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { refreshState(); refreshLog(); }
  });
  navigate(window.location.hash.slice(1), false);
  refreshState();
  window.setInterval(() => { if (!document.hidden) refreshState(); }, 10000);
  window.setInterval(() => {
    if (!document.hidden && selectedJob && page === "jobs" && (jobDetail?.status === "running" || !jobDetail || logError)) refreshLog();
  }, 2000);
})();
