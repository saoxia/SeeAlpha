"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  HARNESS_FINISHED, harnessArtifacts, harnessCancel, harnessContinue, harnessGet,
  harnessHistory, harnessImport, harnessStart, harnessStatus, harnessTaskStorageKey,
  harnessTimeline, type HarnessArtifact, type HarnessEvent, type HarnessHistoryTask,
  type HarnessStatus, type HarnessTask,
} from "@/app/features/research/harness-api";

const statusName:Record<string,string> = {
  queued:"排队中", starting:"正在启动", running:"研究中", cancelling:"正在取消",
  completed:"已完成", failed:"失败", cancelled:"已取消", interrupted:"已中断",
};
const eventName:Record<string,string> = {
  queued:"加入任务队列", runtime_starting:"启动 Agent", agent_active:"Agent 正在研究",
};

export function HarnessWorkspace() {
  const [status,setStatus] = useState<HarnessStatus|null>(null);
  const [history,setHistory] = useState<HarnessHistoryTask[]>([]);
  const [task,setTask] = useState<HarnessTask|null>(null);
  const [events,setEvents] = useState<HarnessEvent[]>([]);
  const [prompt,setPrompt] = useState("");
  const [exportPath,setExportPath] = useState("");
  const [artifacts,setArtifacts] = useState<HarnessArtifact[]>([]);
  const [busy,setBusy] = useState(false);
  const [loading,setLoading] = useState(true);
  const [error,setError] = useState("");
  const cursor = useRef(0);
  const selected = useRef<string|null>(null);
  const currentId = task?.id ?? null;

  const refreshHistory = useCallback(async () => {
    const items = await harnessHistory();
    setHistory(items);
    return items;
  },[]);

  const select = useCallback(async (id:string) => {
    selected.current=id;
    cursor.current=0;
    setEvents([]);
    setArtifacts([]);
    setError("");
    try {
      const value=await harnessGet(id);
      if(selected.current!==id)return;
      setTask(value);
      try { localStorage.setItem(harnessTaskStorageKey,id); } catch { /* Optional. */ }
    } catch(e) {
      if(selected.current===id) setError(e instanceof Error?e.message:"无法打开研究会话。");
    }
  },[]);

  useEffect(()=>{
    let active=true;
    async function initialize(){
      try {
        const connection=await harnessStatus();
        if(!active)return;
        setStatus(connection);
        if(!connection.enabled||!connection.available)return;
        const items=await harnessHistory();
        if(!active)return;
        setHistory(items);
        let saved:string|null=null;
        try { saved=localStorage.getItem(harnessTaskStorageKey); } catch { /* Optional. */ }
        const id=items.find(item=>item.id===saved)?.id??items[0]?.id;
        if(id)await select(id);
      } catch(e) {
        if(active)setError(e instanceof Error?e.message:"无法连接 Harness。");
      } finally {
        if(active)setLoading(false);
      }
    }
    void initialize();
    return ()=>{active=false;};
  },[select]);

  useEffect(()=>{
    if(!currentId)return;
    let active=true,activeRequest=false;
    const id=currentId;
    async function poll(){
      if(activeRequest)return;
      activeRequest=true;
      try {
        const [next,timeline]=await Promise.all([
          harnessGet(id), harnessTimeline(id,cursor.current),
        ]);
        if(!active||selected.current!==id)return;
        setTask(next);
        if(timeline.events.length){
          cursor.current=timeline.next_cursor;
          setEvents(previous=>[...previous,...timeline.events].slice(-200));
        }
        setHistory(previous=>{
          const nextItem:HarnessHistoryTask={
            id:next.id,status:next.status,turn:next.turn,
            created_at:next.created_at,updated_at:next.updated_at,error:next.error,
          };
          const rest=previous.filter(item=>item.id!==id);
          return [nextItem,...rest].slice(0,50);
        });
      } catch(e) {
        if(active)setError(e instanceof Error?e.message:"无法读取 Harness 任务进度。");
      } finally {
        activeRequest=false;
      }
    }
    void poll();
    const timer=window.setInterval(()=>void poll(),2000);
    return ()=>{active=false;window.clearInterval(timer);};
  },[currentId]);

  useEffect(()=>{
    if(!currentId||task?.status!=="completed"){
      const timer=window.setTimeout(()=>setArtifacts([]),0);
      return ()=>window.clearTimeout(timer);
    }
    const controller=new AbortController();
    void harnessArtifacts(currentId,controller.signal).then(setArtifacts).catch(()=>{
      // A temporarily missing manifest must not erase the completed result.
    });
    return ()=>controller.abort();
  },[currentId,task?.status]);

  const available=Boolean(status?.enabled&&status.available&&status.runtime_configured);
  const running=Boolean(task&&!HARNESS_FINISHED.has(task.status));

  async function submit(){
    const value=prompt.trim();
    if(!value||!available||running||busy)return;
    setBusy(true);setError("");
    try {
      const next=task?.status==="completed"
        ? await harnessContinue(task.id,value)
        : await harnessStart(value);
      selected.current=next.id;cursor.current=0;
      setTask(next);setPrompt("");setEvents([]);setArtifacts([]);
      try {localStorage.setItem(harnessTaskStorageKey,next.id);} catch { /* Optional. */ }
      await refreshHistory().catch(()=>{ /* Current task is still usable. */ });
    } catch(e) {
      setError(e instanceof Error?e.message:"提交结果不明；请先检查历史任务，避免重复运行。");
    } finally {setBusy(false);}
  }

  async function cancel(){
    if(!task||!running||busy)return;
    setBusy(true);setError("");
    try {setTask(await harnessCancel(task.id));}
    catch(e){setError(e instanceof Error?e.message:"取消结果不明，请核实状态。");}
    finally {setBusy(false);}
  }

  async function registerArtifact(){
    if(!task||task.status!=="completed"||!exportPath.trim()||busy)return;
    setBusy(true);setError("");
    try {
      await harnessImport(task.id,exportPath.trim());
      setArtifacts(await harnessArtifacts(task.id));
      setExportPath("");
    } catch(e) {
      setError(e instanceof Error?e.message:"登记成果失败。");
    } finally {setBusy(false);}
  }

  function fresh(){
    selected.current=null;cursor.current=0;
    setTask(null);setEvents([]);setArtifacts([]);
    setError("");
    try {localStorage.removeItem(harnessTaskStorageKey);} catch { /* Optional. */ }
  }

  return <div className="research-harness">
    <div className="settings-card-head">
      <span>06</span>
      <div>
        <h2>AI Harness 研究工作台</h2>
        <p>独立常驻 Harness · 任务队列 · 可续接 Session · 研究成果</p>
      </div>
      <i>HARNESS</i>
    </div>
    {loading&&<p className="research-note">正在检查 Harness 服务…</p>}
    {!loading&&status&&<p className="research-note" role="status">
      {!status.enabled?"尚未启用：需在 Platform 配置 HARNESS_ENABLED、HARNESS_URL 和 HARNESS_API_TOKEN。" :
        !status.available?"Harness 连接失败或协议不兼容。其他交易与研究功能不受影响。" :
        !status.runtime_configured?"Harness 已连接，但没有配置 DeepSeek 测试模型凭据。" :
        "Harness 已连接 · 单容器常驻 · 任务串行排队"}
    </p>}
    {error&&<p className="settings-feedback error" role="alert">{error}</p>}
    <div className="research-harness-layout">
      <aside className="research-harness-history" aria-label="研究会话历史">
        <div className="research-harness-history-head">
          <b>研究会话</b>
          <button type="button" onClick={()=>{void refreshHistory().catch(e=>setError(e instanceof Error?e.message:"无法刷新历史。"));}}
            disabled={!status?.available||busy}>刷新</button>
        </div>
        <button type="button" onClick={fresh} className={!task?"active":""}>＋ 新研究会话</button>
        {history.length===0&&<p className="research-note">暂无历史会话。</p>}
        {history.map(item=><button type="button" key={item.id}
          className={item.id===currentId?"active":""}
          onClick={()=>void select(item.id)}>
          <span>{item.id.slice(0,8)} · {statusName[item.status]??item.status}</span>
          <small>{item.updated_at?.slice(0,19)??""} · 第 {item.turn} 轮</small>
        </button>)}
      </aside>
      <div className="research-harness-main">
        <div className="research-harness-form">
          <label htmlFor="harness-prompt">{task?.status==="completed"?"继续当前研究会话":"研究目标"}</label>
          <textarea id="harness-prompt" value={prompt} rows={5}
            onChange={e=>setPrompt(e.target.value)} maxLength={20000}
            placeholder="描述研究目标和数据假设。当前仅提供本地 Agent 研究能力，不会自动获得 Engine 数据和实盘交易权限。"
            disabled={!available||running||busy}/>
          <div className="research-harness-controls">
            <button type="button" className="primary" disabled={!available||!prompt.trim()||running||busy}
              onClick={()=>void submit()}>
              {busy?"处理中…":task?.status==="completed"?"继续会话":"开始研究"}
            </button>
            {running&&<button type="button" disabled={busy} onClick={()=>void cancel()}>取消任务</button>}
          </div>
        </div>
        {task&&<section className="research-harness-task" aria-label="Harness 研究结果">
          <div className="research-log-head">
            <b>任务 {task.id}</b>
            <span>{statusName[task.status]??task.status} · 第 {task.turn} 轮</span>
          </div>
          {task.error&&<p className="settings-feedback error">任务错误代码：{task.error}</p>}
          {events.length>0&&<div className="research-harness-events" aria-label="研究进度">
            {events.map(event=><p key={event.sequence}>
              {event.timestamp?.slice(11,19)} · {event.type==="terminal"
                ? (statusName[event.status??""]??"任务结束")
                : event.type==="artifact_ready"?"新成果已登记"
                : (eventName[event.stage??""]??"研究进度更新")}
              {event.notifications? ` · ${event.notifications} 次活动通知` : ""}
            </p>)}
          </div>}
          {task.final_response?
            <pre className="research-log" aria-label="Harness 最终研究结果">{task.final_response}</pre>:
            <p className="research-note">{running?"正在运行，进度和结果自动刷新。":"本轮没有最终回答。"}</p>}
          {task.status==="completed"&&<div className="research-harness-artifacts">
            <h3>研究成果（下载为附件）</h3>
            {artifacts.length===0&&<p className="research-note">尚无已登记成果。</p>}
            {artifacts.map(item=><p key={item.artifact_id}>
              <a href={`/api/harness/tasks/${encodeURIComponent(task.id)}/artifacts/${encodeURIComponent(item.artifact_id)}`}
                download>{item.filename||item.artifact_id}</a>
              {" · "}{item.kind}{" · "}{Math.ceil((item.size||0)/1024)} KiB
            </p>)}
            <div className="research-harness-import">
              <label htmlFor="harness-export">登记研究文件（由 Agent 输出到 exports/ 目录）</label>
              <input id="harness-export" value={exportPath} onChange={e=>setExportPath(e.target.value)}
                placeholder="exports/report.csv" disabled={busy}/>
              <button type="button" onClick={()=>void registerArtifact()}
                disabled={busy||!exportPath.trim()}>登记为成果</button>
            </div>
          </div>}
        </section>}
      </div>
    </div>
    <p className="research-note">当前为管理员单用户开发模式，Agent 的自由 Shell 并非多租户强隔离。不能输入真实交易密钥、生产敏感数据；Engine Research 工具和策略自动发布仍未接通。任务由 Harness 持久保存，所有成果需经过人工审查。</p>
  </div>;
}
