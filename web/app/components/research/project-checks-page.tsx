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
import { artifactHref, checkHeadline, hasRunningJob, statusLabel } from "../../features/research/model";

export function ProjectChecksPage() {
  return (
    <section className="settings-page research-page" id="project-checks" aria-label="项目检查与运维任务">
      <div className="settings-card">
        <h1>项目检查与运维任务</h1>
        <ProjectChecks />
      </div>
    </section>
  );
}

export function ProjectChecks() {
  const [state, setState] = useState<ResearchState | null>(null);
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<ResearchJob | null>(null);
  const refresh = useCallback(async () => {
    try {
      const next = await fetchResearchState();
      setState(next);
      setLoadError("");
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "无法读取检查状态。");
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
        if (active) setLoadError(error instanceof Error ? error.message : "无法读取检查状态。");
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

  async function submit(action: string) {
    setSubmitting(true);
    setNotice(null);
    try {
      const job = await startResearchJob(action);
      setSelectedId(job.id);
      setSelected(job);
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
  const actions = state?.actions ?? [];
  const detail = selected?.id === selectedId ? selected : null;

  return (
    <>
      {loadError && <p role="alert" className="settings-feedback error">{loadError}</p>}
      {notice && <p role={notice.tone === "error" ? "alert" : "status"} className={`settings-feedback ${notice.tone}`}>{notice.text}</p>}
      <section aria-label="项目检查">
        <h2>项目检查</h2>
        {checks && <p><strong>{checks.title}</strong> · {checks.detail}</p>}
        {!state && !loadError && <p role="status">正在读取检查状态…</p>}
        <div className="settings-head-actions">
          {actions.map((action) => (
            <button key={action.id} type="button" className="primary" title={action.description}
              disabled={submitting || running} onClick={() => void submit(action.id)}>
              {action.label}
            </button>
          ))}
        </div>
        {running && <p role="status">已有任务运行，结束后可启动下一次检查。</p>}
      </section>
      <section aria-label="任务记录">
        <h2>任务记录</h2>
        <div className="research-jobs">
          {state && <JobList jobs={state.jobs} selectedId={selectedId} onSelect={setSelectedId} />}
          <div>
            <div className="research-log-head">
              <b>{detail?.label || "任务日志"}</b>
              <span>{detail ? statusLabel(detail.status) : selectedId ? "读取中…" : "尚未选择任务"}</span>
              {detail?.status === "running" && (
                <button type="button" disabled={submitting} onClick={() => void cancel()}>停止任务</button>
              )}
            </div>
            {detail?.error && <p role="status" className="settings-feedback error">{detail.error}</p>}
            <pre className="research-log" aria-label="任务输出日志">{detail?.log || (selectedId ? (detail ? "暂无输出。" : "正在读取日志…") : "选择任务查看日志。")}</pre>
            {detail?.log_truncated && <p className="research-note">日志只显示末尾部分。</p>}
            <div className="research-links">
              {(detail?.report_urls ?? []).map((url) => {
                const href = artifactHref(url);
                return href ? <a key={url} href={href} target="_blank" rel="noreferrer">打开本次报告</a> : null;
              })}
            </div>
          </div>
        </div>
      </section>
    </>
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
  if (jobs.length === 0) return <p className="exchange-empty">暂无任务记录。</p>;
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
