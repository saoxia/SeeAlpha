"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  HARNESS_FINISHED, engineCancelDataJob, engineDataJobs, engineDataPlan, enginePrepareData,
  engineResetDevelopment, harnessArtifacts, harnessCancel, harnessContinue, harnessGet,
  harnessHistory, harnessImport, harnessStart, harnessStatus, harnessTaskStorageKey,
  harnessTimeline, type HarnessArtifact, type HarnessEvent, type HarnessHistoryTask,
  type HarnessStatus, type HarnessTask, type ResearchSelection,
  type DevelopmentDataJob, type DevelopmentDataPlan, type DevelopmentEngineSelection,
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
  const [useResearch,setUseResearch] = useState(false);
  const [researchTenant,setResearchTenant] = useState("");
  const [researchAccount,setResearchAccount] = useState("");
  const [researchDataset,setResearchDataset] = useState("futures_um:ETHUSDT:1m");
  const [exportPath,setExportPath] = useState("");
  const [dataStart,setDataStart] = useState("");
  const [dataEnd,setDataEnd] = useState("");
  const [enginePlan,setEnginePlan] = useState<DevelopmentDataPlan|null>(null);
  const [engineJobs,setEngineJobs] = useState<DevelopmentDataJob[]>([]);
  const [engineBusy,setEngineBusy] = useState(false);
  const [engineMessage,setEngineMessage] = useState("");
  const [resetConfirmation,setResetConfirmation] = useState("");
  const [artifacts,setArtifacts] = useState<HarnessArtifact[]>([]);
  const [busy,setBusy] = useState(false);
  const [loading,setLoading] = useState(true);
  const [error,setError] = useState("");
  const cursor = useRef(0);
  const selected = useRef<string|null>(null);
  const currentId = task?.id ?? null;
  const engineSelection = useCallback(():DevelopmentEngineSelection => {
    const tenantId=Number(researchTenant);
    if (!Number.isSafeInteger(tenantId)||tenantId<=0
      ||!researchAccount.trim()||!researchDataset.trim()) {
      throw new Error("请填写有效的开发租户、虚拟账户和授权数据集。");
    }
    return {tenant_id:tenantId,account_id:researchAccount.trim(),dataset_ref:researchDataset.trim()};
  },[researchTenant,researchAccount,researchDataset]);

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

  useEffect(()=>{
    if(!useResearch||!researchTenant||!researchAccount||!researchDataset||!status?.available)return;
    let active=true;
    async function refresh(){
      try {
        const next=await engineDataJobs(engineSelection());
        if(active)setEngineJobs(next);
      } catch {
        // Configuration can be incomplete before the user selects an authorized scope.
      }
    }
    void refresh();
    const timer=window.setInterval(()=>void refresh(),3000);
    return ()=>{active=false;window.clearInterval(timer);};
  },[useResearch,researchTenant,researchAccount,researchDataset,status?.available,engineSelection]);

  const available=Boolean(status?.enabled&&status.available&&status.runtime_configured);
  const running=Boolean(task&&!HARNESS_FINISHED.has(task.status));

  async function submit(){
    const value=prompt.trim();
    if(!value||!available||running||busy)return;
    setBusy(true);setError("");
    try {
      const selection:ResearchSelection|undefined = useResearch && !task
        ? {
            research_tenant_id:Number(researchTenant),
            research_account_id:researchAccount.trim(),
            research_dataset_ref:researchDataset.trim(),
          }
        : undefined;
      if (selection && (
        !Number.isSafeInteger(selection.research_tenant_id)
        || selection.research_tenant_id <= 0 || !selection.research_account_id
        || !selection.research_dataset_ref
      )) throw new Error("请填写有效的开发租户、虚拟账户和授权数据集。");
      const next=task?.status==="completed"
        ? await harnessContinue(task.id,value)
        : await harnessStart(value,selection);
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

  async function manageData(action:"plan"|"prepare"){
    if(engineBusy||busy)return;
    setEngineBusy(true);setEngineMessage("");
    try {
      const selection=engineSelection();
      if(!dataStart||!dataEnd||dataStart>=dataEnd) {
        throw new Error("请选择有效的 UTC 起止日期，结束日期不包含在下载范围内。");
      }
      const payload={
        ...selection,start:dataStart+"T00:00:00Z",end:dataEnd+"T00:00:00Z",
      };
      if(action==="plan"){
        setEnginePlan(await engineDataPlan(payload));
        setEngineMessage("已获取开发 Engine 数据准备计划。");
      } else {
        const job=await enginePrepareData(payload);
        setEnginePlan(null);
        setEngineJobs(await engineDataJobs(selection));
        setEngineMessage("数据准备任务已提交："+job.id);
      }
    } catch(e){
      setEngineMessage(e instanceof Error?e.message:"开发 Engine 操作失败。");
    } finally {setEngineBusy(false);}
  }

  async function cancelEngineJob(id:string){
    if(engineBusy)return;
    setEngineBusy(true);setEngineMessage("");
    try{
      const selection=engineSelection();
      await engineCancelDataJob(selection,id);
      setEngineJobs(await engineDataJobs(selection));
      setEngineMessage("已请求取消历史数据任务。");
    }catch(e){setEngineMessage(e instanceof Error?e.message:"取消失败。");}
    finally{setEngineBusy(false);}
  }

  async function resetEngine(){
    if(engineBusy||busy||running)return;
    setEngineBusy(true);setEngineMessage("");
    try{
      const selection=engineSelection();
      const result=await engineResetDevelopment(selection.tenant_id,resetConfirmation);
      setEnginePlan(null);setEngineJobs([]);setResetConfirmation("");
      setEngineMessage("开发 Engine 已恢复初始运行状态，历史数据已保留。恢复备份编号："+result.backup);
    }catch(e){setEngineMessage(e instanceof Error?e.message:"重置失败，请核实 Engine 状态。");}
    finally{setEngineBusy(false);}
  }

  function fresh(){
    selected.current=null;cursor.current=0;
    setTask(null);setEvents([]);setArtifacts([]);
    setError("");
    try {localStorage.removeItem(harnessTaskStorageKey);} catch { /* Optional. */ }
  }

  return <div className="research-harness">
    {loading&&<p className="research-note">正在检查 Harness 服务…</p>}
    {!loading&&status&&<p className="research-note" role="status">
      {!status.enabled?"此部署尚未启用 AI Harness。" :
        !status.available?"Harness 连接失败或协议不兼容。" :
        !status.runtime_configured?"Harness 已连接，但没有配置 DeepSeek 测试模型凭据。" :
        "Harness 已连接"}
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
            placeholder="描述研究目标和数据假设。可选开启已授权开发回测工具；AI 无实盘交易权限。"
            disabled={!available||running||busy}/>
          <details>
            <summary>开发 Engine：研究授权、历史数据和状态重置</summary>
            <label><input type="checkbox" checked={useResearch}
              onChange={e=>setUseResearch(e.target.checked)} disabled={busy||engineBusy}/>
              授权当前 AI 任务调用开发数据与模拟回测</label>
            {useResearch&&<div>
              <label>开发租户 ID<input value={researchTenant}
                inputMode="numeric" onChange={e=>{setResearchTenant(e.target.value);setEnginePlan(null);setEngineJobs([]);}}
                disabled={busy}/></label>
              <label>虚拟账户 ID<input value={researchAccount}
                onChange={e=>{setResearchAccount(e.target.value);setEnginePlan(null);setEngineJobs([]);}} disabled={busy}/></label>
              <label>数据集标识<input value={researchDataset}
                onChange={e=>{setResearchDataset(e.target.value);setEnginePlan(null);setEngineJobs([]);}} disabled={busy}
                placeholder="futures_um:ETHUSDT:1m"/></label>
              <p className="research-note">需要该开发租户拥有虚拟账户和历史数据订阅；
              授权仅在开始新会话时绑定，已有会话继续使用原授权。不能执行实盘交易。</p>
              <section aria-label="开发 Engine 历史数据准备">
                <h3>回测历史数据获取与准备</h3>
                <p className="research-note">在当前开发 Engine 的独立数据卷下载、补缺并生成所选数据集的 Catalog。
                日期按 UTC 计算，结束日期不包含在内；单任务最多 31 天。</p>
                <label>开始日期（UTC）<input type="date" value={dataStart}
                  onChange={e=>{setDataStart(e.target.value);setEnginePlan(null);}} disabled={engineBusy}/></label>
                <label>结束日期（UTC，不含）<input type="date" value={dataEnd}
                  onChange={e=>{setDataEnd(e.target.value);setEnginePlan(null);}} disabled={engineBusy}/></label>
                <div className="research-harness-controls">
                  <button type="button" disabled={engineBusy||busy||!dataStart||!dataEnd}
                    onClick={()=>void manageData("plan")}>检查缺失数据</button>
                  <button type="button" disabled={engineBusy||busy||!dataStart||!dataEnd}
                    onClick={()=>void manageData("prepare")}>获取并准备历史数据</button>
                </div>
                {enginePlan&&<p className="research-note" role="status">
                  预计归档天数：{enginePlan.archive_days??"未知"}；待补数据天数：{enginePlan.missing_days??"未知"}。
                  {enginePlan.message??""}
                </p>}
                {engineJobs.length>0&&<div aria-label="历史数据任务进度">
                  <h3>准备任务</h3>
                  {engineJobs.slice(0,10).map(job=><p key={job.id} className="research-note">
                    {job.id.slice(0,8)} · {job.status} · {job.progress??""}
                    {job.error?" · "+job.error:""}
                    {job.status==="running"&&<button type="button" disabled={engineBusy}
                      onClick={()=>void cancelEngineJob(job.id)}>取消</button>}
                  </p>)}
                </div>}
              </section>
              <section aria-label="重置开发 Engine">
                <h3>恢复开发 Engine 初始运行状态</h3>
                <p className="research-note">停止并重建当前开发 Engine，清空运行记录和 Engine 内任务状态。
                保留已下载的历史数据、Platform 账户和策略授权记录、Harness 研究会话。
                原运行状态会在服务器私有目录留存恢复备份。请先停止全部数据、回测及 AI 任务；
                如有已下发的策略包，重置后可能需要重新下发。</p>
                <label>输入 RESET ENGINE {researchTenant||"租户ID"} 确认
                  <input value={resetConfirmation} onChange={e=>setResetConfirmation(e.target.value)}
                    placeholder={researchTenant?"RESET ENGINE "+researchTenant:"请先填写租户 ID"}
                    disabled={engineBusy||busy||running}/></label>
                <button type="button" disabled={engineBusy||busy||running||
                  resetConfirmation!=="RESET ENGINE "+researchTenant}
                  onClick={()=>void resetEngine()}>恢复初始状态（保留历史数据）</button>
              </section>
              {engineMessage&&<p className="research-note" role="status">{engineMessage}</p>}
            </div>}
          </details>}
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
    <p className="research-note">AI Harness 是用户研究工作台，开发 Engine 管理仅操作当前用户已授权的本机开发租户；与正式 Engine 隔离。不要输入真实交易密钥或生产敏感数据；研究成果需要人工审查。</p>
  </div>;
}
