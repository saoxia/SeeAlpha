"use client";

import { useEffect, useState } from "react";
import {
  HARNESS_FINISHED, harnessArtifacts, harnessCancel, harnessContinue, harnessGet,
  harnessStart, harnessStatus, harnessTaskStorageKey,
  type HarnessArtifact, type HarnessStatus, type HarnessTask,
} from "@/app/features/research/harness-api";

const stages: Record<string,string> = {
  queued: "排队中", starting: "启动中", running: "运行中", cancelling: "取消中",
  completed: "已完成", failed: "失败", cancelled: "已取消", interrupted: "已中断",
};

export function HarnessWorkspace() {
  const [status, setStatus] = useState<HarnessStatus | null>(null);
  const [task, setTask] = useState<HarnessTask | null>(null);
  const [prompt, setPrompt] = useState("");
  const [artifacts, setArtifacts] = useState<HarnessArtifact[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    async function initialize() {
      try {
        const next = await harnessStatus(controller.signal);
        if (controller.signal.aborted) return;
        setStatus(next);
        const id = localStorage.getItem(harnessTaskStorageKey);
        if (id && /^[a-f0-9-]{36}$/.test(id)) {
          const previous = await harnessGet(id, controller.signal);
          if (!controller.signal.aborted) setTask(previous);
        }
      } catch (e) {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "无法连接 Harness。");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void initialize();
    return () => controller.abort();
  }, []);

  const currentId = task?.id;
  const currentStatus = task?.status;
  useEffect(() => {
    if (!currentId || !currentStatus || HARNESS_FINISHED.has(currentStatus)) return;
    let active = true;
    const poll = () => {
      void harnessGet(currentId).then(value => {
        if (active) {setTask(value);setError("");}
      }).catch(e => {
        if (active) setError(e instanceof Error ? e.message : "无法读取任务状态。");
      });
    };
    const timer = window.setInterval(poll, 2000);
    return () => {active = false;window.clearInterval(timer);};
  }, [currentId, currentStatus]);

  useEffect(() => {
    if (!currentId || currentStatus !== "completed") {
      const timer = window.setTimeout(() => setArtifacts([]), 0);
      return () => window.clearTimeout(timer);
    }
    const controller = new AbortController();
    void harnessArtifacts(currentId,controller.signal).then(setArtifacts).catch(() => {
      // The completed answer remains visible if the optional manifest is unavailable.
    });
    return () => controller.abort();
  }, [currentId, currentStatus]);

  const available = status?.enabled && status.available && status.runtime_configured;
  const running = task && !HARNESS_FINISHED.has(task.status);

  async function submit() {
    const value = prompt.trim();
    if (!value || !available || busy || running) return;
    setBusy(true);setError("");
    try {
      // Continue only a completed Session. Any other terminal state starts a new task.
      const next = task?.status === "completed"
        ? await harnessContinue(task.id, value)
        : await harnessStart(value);
      setTask(next);
      setPrompt("");
      setArtifacts([]);
      localStorage.setItem(harnessTaskStorageKey,next.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "提交结果不明，请先核实任务，不要盲目重试。");
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (!task || !running || busy) return;
    setBusy(true);setError("");
    try {setTask(await harnessCancel(task.id));}
    catch(e){setError(e instanceof Error ? e.message : "取消请求结果不明。");}
    finally {setBusy(false);}
  }

  function fresh() {
    setTask(null);
    setArtifacts([]);
    localStorage.removeItem(harnessTaskStorageKey);
    setError("");
  }

  return <div className="research-harness">
    <div className="settings-card-head">
      <span>06</span>
      <div>
        <h2>AI Harness 研究工作台</h2>
        <p>使用独立、常驻的单 Docker Harness 执行 AI 研究任务；现阶段仅管理员本地开发，不接入 Engine 研究工具或实盘。</p>
      </div>
      <i>HARNESS</i>
    </div>
    {loading && <p className="research-note">正在检查 Harness 服务…</p>}
    {!loading && status && <p className="research-note" role="status">
      {!status.enabled ? "尚未启用：需在 SeeAlpha Platform 配置 HARNESS_ENABLED、HARNESS_URL 和 HARNESS_API_TOKEN。" :
        !status.available ? "Harness 服务不可达或协议不兼容，其他研究功能不受影响。" :
        !status.runtime_configured ? "Harness 已连接，但还没有配置 AI 模型凭据。" :
        "Harness 已连接 · 单容器常驻运行 · 任务串行排队"}
    </p>}
    {error && <p className="settings-feedback error" role="alert">{error}</p>}
    <div className="research-harness-form">
      <label htmlFor="harness-prompt">研究目标</label>
      <textarea id="harness-prompt" value={prompt} rows={5}
        onChange={e=>setPrompt(e.target.value)} maxLength={20000}
        placeholder="例如：设计并解释一组时间序列因子检验。此阶段不包含真实 Engine 数据访问。"
        disabled={!available || Boolean(running) || busy}/>
      <div className="research-harness-controls">
        <button type="button" className="primary" disabled={!available || !prompt.trim() || Boolean(running) || busy}
          onClick={()=>void submit()}>{busy?"提交中…":task?.status==="completed"?"继续当前会话":"开始研究"}</button>
        {running && <button type="button" disabled={busy} onClick={()=>void cancel()}>取消任务</button>}
        {task && !running && <button type="button" disabled={busy} onClick={fresh}>新建会话</button>}
      </div>
    </div>
    {task && <section className="research-harness-task" aria-label="Harness 任务状态">
      <div className="research-log-head">
        <b>任务 {task.id}</b><span>{stages[task.status]??task.status} · 第 {task.turn} 轮</span>
      </div>
      {task.error && <p className="settings-feedback error">错误代码：{task.error}</p>}
      {task.final_response
        ? <pre className="research-log" aria-label="Harness 研究结果">{task.final_response}</pre>
        : <p className="research-note">{running?"运行中，结果会自动刷新。":"本轮没有最终研究结果。"}</p>}
      {artifacts.length>0 && <div className="research-harness-artifacts">
        <h3>成果清单（未审核）</h3>
        {artifacts.map(item=><p key={item.artifact_id}>
          {item.filename||item.artifact_id} · {item.kind} · {Math.ceil((item.size||0)/1024)} KiB
        </p>)}
      </div>}
    </section>}
    <p className="research-note">此入口仅用于可信管理员的本地研究。研究任务的执行代码仍在单容器环境内运行，不是多租户安全沙箱；任务记录目前由 Harness 持久保存，本浏览器仅记住最近一项任务 ID。请勿提交生产凭据或敏感数据。</p>
  </div>;
}
