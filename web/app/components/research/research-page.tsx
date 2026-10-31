"use client";

import { useCallback, useEffect, useState } from "react";
import {
  cancelResearchJob,
  fetchResearchJob,
  fetchResearchState,
  startResearchJob,
  type ResearchJob,
  type ResearchState,
} from "../../features/research/api";
import {
  artifactHref,
  checkHeadline,
  hasRunningJob,
  reportMatches,
  statusLabel,
} from "../../features/research/model";

type Section = "overview" | "checks" | "backtest" | "reports" | "jobs";

const sections: Array<{ id: Section; number: string; label: string; description: string }> = [
  { id: "overview", number: "01", label: "总览", description: "检查与最近任务" },
  { id: "checks", number: "02", label: "项目检查", description: "规范与测试" },
  { id: "backtest", number: "03", label: "策略回测", description: "本地 Catalog" },
  { id: "reports", number: "04", label: "报告", description: "已生成结果" },
  { id: "jobs", number: "05", label: "任务记录", description: "日志与取消" },
];

export function ResearchPage() {
  const [section, setSection] = useState<Section>("overview");
  const [state, setState] = useState<ResearchState | null>(null);
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<ResearchJob | null>(null);
  const [query, setQuery] = useState("");
  const [strategy, setStrategy] = useState("");
  const [start, setStart] = useState("2026-08-01");
  const [end, setEnd] = useState("2026-08-08");
  const [interval, setIntervalValue] = useState("1m");

  const refresh = useCallback(async () => {
    try {
      const next = await fetchResearchState();
      setState(next);
      setLoadError("");
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "无法读取研究状态。");
    }
  }, []);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const next = await fetchResearchState();
        if (!active) return;
        setState(next);
        setLoadError("");
      } catch (error) {
        if (active) setLoadError(error instanceof Error ? error.message : "无法读取研究状态。");
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 10_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    let active = true;
    const load = async () => {
      try {
        const job = await fetchResearchJob(selectedId);
        if (active) setSelected(job);
      } catch (error) {
        if (active) setNotice({ tone: "error", text: error instanceof Error ? error.message : "无法读取任务日志。" });
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 2_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [selectedId]);

  async function submit(action: string, parameters: Record<string, string> = {}) {
    setSubmitting(true);
    setNotice(null);
    try {
      const job = await startResearchJob(action, parameters);
      setSelectedId(job.id);
      setSelected(job);
      setSection("jobs");
      setNotice({ tone: "success", text: `已开始：${job.label || statusLabel(job.status)}` });
      await refresh();
    } catch (error) {
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "无法启动任务。" });
    } finally {
      setSubmitting(false);
    }
  }

  async function cancel() {
    if (!selectedId) return;
    setSubmitting(true);
    setNotice(null);
    try {
      const job = await cancelResearchJob(selectedId);
      setSelected(job);
      setNotice({ tone: "success", text: "已请求停止任务。未完成的输出不能视为完成报告。" });
      await refresh();
    } catch (error) {
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "无法取消任务。" });
    } finally {
      setSubmitting(false);
    }
  }

  const running = hasRunningJob(state?.jobs ?? []);
  const checks = state ? checkHeadline(state.checks) : null;
  const reports = (state?.reports ?? []).filter((item) => reportMatches(item, query));
  const chosen = state?.strategies.find((item) => item.id === strategy);

  return (
    <section className="settings-page research-page" id="research">
      <div className="settings-layout">
        <aside className="settings-sidebar" aria-label="研究目录">
          <div className="settings-sidebar-head">
            <span>RESEARCH</span>
            <b>研究与任务</b>
          </div>
          <nav>
            {sections.map((item) => (
              <button
                key={item.id}
                type="button"
                className={section === item.id ? "active" : ""}
                aria-current={section === item.id ? "page" : undefined}
                onClick={() => setSection(item.id)}
              >
                <span>{item.number}</span>
                <div>
                  <b>{item.label}</b>
                  <small>{item.description}</small>
                </div>
                <i aria-hidden="true">→</i>
              </button>
            ))}
          </nav>
        </aside>

        <main className="settings-content">
          <div className="settings-card">
            {loadError && <p role="alert" className="settings-feedback error">{loadError}</p>}
            {notice && <p role={notice.tone === "error" ? "alert" : "status"} className={`settings-feedback ${notice.tone}`}>{notice.text}</p>}

            {section === "overview" && (
              <>
                <div className="settings-card-head">
                  <span>01</span>
                  <div>
                    <h2>运行总览</h2>
                    <p>项目检查、可用策略和最近任务。过期检查不能视为当前正常。</p>
                  </div>
                  <i>WORKSPACE</i>
                </div>
                <div className="research-metrics">
                  <article>
                    <small>项目检查</small>
                    <b>{checks?.title ?? "正在读取…"}</b>
                    <span>{checks?.detail}</span>
                  </article>
                  <article>
                    <small>任务</small>
                    <b>{running ? "有任务运行" : "空闲"}</b>
                    <span>同一时间只运行一个检查或回测。</span>
                  </article>
                  <article>
                    <small>报告</small>
                    <b>{state?.report_count ?? "—"}</b>
                    <span>仅列出当前可访问的报告文件。</span>
                  </article>
                </div>
                <div className="settings-head-actions research-actions">
                  {(state?.actions ?? []).filter((item) => item.id !== "backtest").map((item) => (
                    <button key={item.id} type="button" className="primary" disabled={submitting || running || !state} onClick={() => void submit(item.id)}>
                      {item.label}
                    </button>
                  ))}
                </div>
                <JobList jobs={state?.jobs.slice(0, 6) ?? []} selectedId={selectedId} onSelect={(id) => { setSelectedId(id); setSection("jobs"); }} />
              </>
            )}

            {section === "checks" && (
              <>
                <div className="settings-card-head">
                  <span>02</span>
                  <div>
                    <h2>项目检查</h2>
                    <p>快速检查核验冻结来源与代码规范，完整检查额外运行全量测试。检查最多 30 分钟。</p>
                  </div>
                  <i>CHECKS</i>
                </div>
                <div className="research-actions-list">
                  {(state?.actions ?? []).filter((item) => item.id !== "backtest").map((item) => (
                    <article key={item.id}>
                      <b>{item.label}</b>
                      <p>{item.description}</p>
                      <button type="button" className="primary" disabled={submitting || running || !state} onClick={() => void submit(item.id)}>
                        开始
                      </button>
                    </article>
                  ))}
                </div>
              </>
            )}

            {section === "backtest" && (
              <>
                <div className="settings-card-head">
                  <span>03</span>
                  <div>
                    <h2>策略回测</h2>
                    <p>使用策略现行参数和已有本地 K 线。结束时间不包含在区间内，单次最多 366 天。不会下载或补齐数据。</p>
                  </div>
                  <i>BACKTEST</i>
                </div>
                <form className="form-grid" onSubmit={(event) => {
                  event.preventDefault();
                  void submit("backtest", { strategy, start, end, interval });
                }}>
                  <label className="field field-wide">
                    <span>回测策略</span>
                    <select value={strategy} required onChange={(event) => setStrategy(event.target.value)}>
                      <option value="">请选择策略</option>
                      {(state?.strategies ?? []).map((item) => (
                        <option key={item.id} value={item.id}>{item.label}</option>
                      ))}
                    </select>
                  </label>
                  <p className="research-note">{chosen?.description || "BTC、ETH 的趋势跟随与均值回归多空入口。"}</p>
                  <label className="field">
                    <span>开始日期 UTC</span>
                    <input type="date" required value={start} onChange={(event) => setStart(event.target.value)} />
                  </label>
                  <label className="field">
                    <span>结束日期 UTC</span>
                    <input type="date" required value={end} onChange={(event) => setEnd(event.target.value)} />
                  </label>
                  <label className="field">
                    <span>K 线周期</span>
                    <select value={interval} onChange={(event) => setIntervalValue(event.target.value)}>
                      <option value="1m">1 分钟</option>
                      <option value="5m">5 分钟</option>
                    </select>
                  </label>
                  <div className="settings-head-actions research-actions">
                    <button type="submit" className="primary" disabled={submitting || running || !state}>开始回测</button>
                  </div>
                </form>
              </>
            )}

            {section === "reports" && (
              <>
                <div className="settings-card-head">
                  <span>04</span>
                  <div>
                    <h2>报告中心</h2>
                    <p>打开已有单策略和组合回测报告。数据盘不可用时不展示无法访问的文件，已保存的索引不会被清除。</p>
                  </div>
                  <i>REPORTS</i>
                </div>
                <label className="field">
                  <span>搜索</span>
                  <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="策略、名称或运行编号" aria-label="搜索报告" />
                </label>
                {reports.length === 0 ? <p className="exchange-empty">没有可打开的报告。</p> : (
                  <ul className="research-list">
                    {reports.map((item) => {
                      const href = artifactHref(item.url);
                      const summary = artifactHref(item.summary_url);
                      return (
                        <li key={item.url}>
                          <b>{item.strategy}</b>
                          <span>{item.run_id}</span>
                          <small>{item.modified_at}</small>
                          {href && <a href={href} target="_blank" rel="noreferrer">打开报告</a>}
                          {summary && <a href={summary} target="_blank" rel="noreferrer">统计</a>}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </>
            )}

            {section === "jobs" && (
              <>
                <div className="settings-card-head">
                  <span>05</span>
                  <div>
                    <h2>任务记录</h2>
                    <p>状态、输出日志和取消。回测最多两小时，超时会终止。重启后未完成任务标记为中断，不会自动恢复。</p>
                  </div>
                  <i>JOBS</i>
                </div>
                <div className="research-jobs">
                  <JobList jobs={state?.jobs ?? []} selectedId={selectedId} onSelect={setSelectedId} />
                  <div>
                    <div className="research-log-head">
                      <b>{selected?.label || "任务日志"}</b>
                      <span>{selected ? statusLabel(selected.status) : "尚未选择任务"}</span>
                      {selected?.status === "running" && (
                        <button type="button" disabled={submitting} onClick={() => void cancel()}>停止任务</button>
                      )}
                    </div>
                    {selected?.error && <p role="status" className="settings-feedback error">{selected.error}</p>}
                    <pre className="research-log" aria-label="任务输出日志">{selected?.log || "选择一条任务后，这里显示最近的输出。"}</pre>
                    {selected?.log_truncated && <p className="research-note">日志只显示末尾部分。</p>}
                    <div className="research-links">
                      {(selected?.report_urls ?? []).map((url) => {
                        const href = artifactHref(url);
                        return href ? <a key={url} href={href} target="_blank" rel="noreferrer">打开本次报告</a> : null;
                      })}
                    </div>
                  </div>
                </div>
              </>
            )}
          </div>
        </main>
      </div>
    </section>
  );
}

function JobList({
  jobs,
  selectedId,
  onSelect,
}: {
  jobs: ResearchJob[];
  selectedId: string | null;
  onSelect: (identity: string) => void;
}) {
  if (jobs.length === 0) return <p className="exchange-empty">暂无任务记录。可以从总览发起检查，或创建一次回测。</p>;
  return (
    <ul className="research-list">
      {jobs.map((job) => (
        <li key={job.id}>
          <button type="button" className={job.id === selectedId ? "active" : ""} onClick={() => onSelect(job.id)}>
            <b>{job.label || job.action}</b>
            <span>{statusLabel(job.status)}</span>
            <small>{job.created_at}</small>
          </button>
        </li>
      ))}
    </ul>
  );
}
