"use client";

import { useEffect, useRef, useState } from "react";
import { requestId } from "@/app/features/trade/api";
import { indicatorCatalog, indicatorCategories, indicatorError, isMarketOverlay, knownStudyIntervals, maxIndicators, newIndicator, priceSources, type IndicatorConfig, type IndicatorKind } from "@/app/features/trade/indicators";
import { applyTemplate, saveTemplate, type StudyWorkspace } from "@/app/features/trade/study-workspace";

type Category = "all"|"favorites"|"trend"|"momentum"|"volume"|"depth"|"added"|"templates";
const categories: {key:Category;label:string}[] = [
  {key:"all",label:"全部指标"},{key:"favorites",label:"★ 我的收藏"},
  {key:"trend",label:"趋势指标"},{key:"momentum",label:"动量指标"},
  {key:"volume",label:"成交量"},{key:"depth",label:"市场深度"},
  {key:"added",label:"已添加"},{key:"templates",label:"指标模板"},
];
const kinds=Object.keys(indicatorCatalog) as IndicatorKind[];
const group=(kind:IndicatorKind):Category=>{
  for(const key of ["trend","momentum","volume","depth"] as const){
    if((indicatorCategories[key] as readonly string[]).includes(kind))return key;
  }
  return "all";
};
const isSeparate=(study:IndicatorConfig)=>["RSI","MACD","VOL"].includes(study.kind)||(study.kind==="CUSTOM"&&study.pane==="separate");

export function IndicatorLibrary({configs,onChange,onClose,onEdit,workspace,onWorkspaceChange}:{
  configs:IndicatorConfig[];onChange:(value:IndicatorConfig[])=>void;onClose:()=>void;
  onEdit:(id:string)=>void;workspace:StudyWorkspace;onWorkspaceChange:(next:StudyWorkspace)=>void;
}) {
  const [category,setCategory]=useState<Category>("all");
  const [query,setQuery]=useState("");
  const [templateName,setTemplateName]=useState("");
  const [notice,setNotice]=useState("");
  const search=useRef<HTMLInputElement>(null);
  useEffect(()=>{
    search.current?.focus();
    const close=(event:KeyboardEvent)=>{if(event.key==="Escape")onClose();};
    window.addEventListener("keydown",close);
    return ()=>window.removeEventListener("keydown",close);
  },[onClose]);
  const matches=kinds.filter(kind=>
    (category==="all"||category==="favorites"&&workspace.favorites.includes(kind)||
      category==="added"&&configs.some(study=>study.kind===kind)||
      category!=="favorites"&&category!=="added"&&category!=="templates"&&group(kind)===category)&&
    (kind+" "+indicatorCatalog[kind].name).toLowerCase().includes(query.trim().toLowerCase()));
  const isUnavailable=(kind:IndicatorKind)=>configs.length>=maxIndicators||
    isMarketOverlay(kind)&&configs.some(study=>study.kind===kind);
  function add(kind:IndicatorKind){
    if(isUnavailable(kind))return;
    onChange([...configs,newIndicator(kind,`study-${requestId()}`)]);
    setNotice(`已添加 ${kind}，可继续选择其他指标`);
  }
  function favorite(kind:IndicatorKind){
    const next=workspace.favorites.includes(kind)?workspace.favorites.filter(item=>item!==kind):[...workspace.favorites,kind];
    onWorkspaceChange({...workspace,favorites:next});
  }
  function templateSave(){
    try {
      if(workspace.templates.length>=20&&!workspace.templates.some(t=>t.name===templateName.trim())){setNotice("最多保存 20 个模板");return;}
      onWorkspaceChange(saveTemplate(workspace,templateName,configs,`template-${requestId()}`));
      setTemplateName("");setNotice("已保存到当前浏览器的指标模板");
    } catch(err){setNotice(err instanceof Error?err.message:"保存失败");}
  }
  return <div className="trade-study-modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)onClose();}}>
    <section className="trade-study-modal trade-study-library" role="dialog" aria-modal="true" aria-label="技术指标库">
      <header className="trade-study-modal-title"><div><h2>技术指标</h2><p>搜索、收藏和管理图表指标</p></div><span>{configs.length}/{maxIndicators} 组</span><button type="button" aria-label="关闭指标库" onClick={onClose}>×</button></header>
      <div className="trade-study-search"><span aria-hidden="true">⌕</span><input ref={search} type="search" aria-label="搜索指标" placeholder="搜索指标名称或代码（EMA、布林带…）" value={query} onChange={e=>setQuery(e.target.value)}/>{query&&<button type="button" aria-label="清除搜索" onClick={()=>setQuery("")}>×</button>}</div>
      <div className="trade-study-columns">
        <nav className="trade-study-categories" aria-label="指标分类">{categories.map(item=><button key={item.key} type="button" className={category===item.key?"active":""} aria-current={category===item.key?"page":undefined} onClick={()=>{setCategory(item.key);setNotice("");}}>{item.label}</button>)}</nav>
        <div className="trade-study-results">
          {category==="templates"?<><div className="trade-study-template-form"><label htmlFor="trade-template-name">保存当前图表指标为模板</label><div><input id="trade-template-name" value={templateName} maxLength={40} onChange={e=>setTemplateName(e.target.value)} placeholder="模板名称"/><button type="button" disabled={!templateName.trim()} onClick={templateSave}>保存</button></div></div>
            {workspace.templates.length?workspace.templates.map(template=><article className="trade-study-result" key={template.id}><div className="trade-study-result-text"><strong>{template.name}</strong><small>{template.studies.length} 组指标</small></div><button type="button" onClick={()=>{onChange(applyTemplate(template,kind=>`study-${kind}-${requestId()}`));setNotice("模板已应用");}}>应用</button><button type="button" aria-label={`删除模板 ${template.name}`} onClick={()=>onWorkspaceChange({...workspace,templates:workspace.templates.filter(t=>t.id!==template.id)})}>删除</button></article>):<p className="trade-study-empty">暂无模板。保存当前指标组合后可一键切换。</p>}
          </>:category==="added"?<>
            {configs.map((study,index)=><article className="trade-study-result" key={study.id}><div className="trade-study-result-text"><strong>{study.kind}{study.parameters.length?` (${study.parameters.join(", ")})`:""}</strong><small>{isSeparate(study)?"副图":"主图"} · {study.visible?"显示":"隐藏"}</small></div>
              <button type="button" aria-label={`${study.kind} 上移`} disabled={index===0} onClick={()=>{const next=[...configs];[next[index-1],next[index]]=[next[index],next[index-1]];onChange(next);}}>↑</button>
              <button type="button" aria-label={`${study.kind} 下移`} disabled={index===configs.length-1} onClick={()=>{const next=[...configs];[next[index+1],next[index]]=[next[index],next[index+1]];onChange(next);}}>↓</button>
              <button type="button" onClick={()=>onEdit(study.id)}>设置</button>
              <button type="button" aria-label={`删除 ${study.kind}`} onClick={()=>onChange(configs.filter(c=>c.id!==study.id))}>×</button>
            </article>)}
            {!configs.length&&<p className="trade-study-empty">还未添加指标。请从左侧分类选择。</p>}
          </>:<>
            {matches.map(kind=><article className="trade-study-result" key={kind}>
              <button type="button" className={workspace.favorites.includes(kind)?"trade-study-star active":"trade-study-star"} title="收藏指标" aria-label={workspace.favorites.includes(kind)?`取消收藏 ${kind}`:`收藏 ${kind}`} aria-pressed={workspace.favorites.includes(kind)} onClick={()=>favorite(kind)}>{workspace.favorites.includes(kind)?"★":"☆"}</button>
              <div className="trade-study-result-text"><strong>{kind}</strong><small>{indicatorCatalog[kind].name}</small></div>
              {isMarketOverlay(kind)&&configs.some(c=>c.kind===kind)&&<small className="trade-study-added">已添加</small>}
              <button type="button" className="trade-study-add" disabled={isUnavailable(kind)} aria-label={`添加 ${kind}`} onClick={()=>add(kind)}>＋ 添加</button>
            </article>)}
            {!matches.length&&<p className="trade-study-empty">没有找到匹配的指标。</p>}
            {configs.length>=maxIndicators&&<p className="trade-study-empty">已达到 24 组上限，请先删除现有指标。</p>}
          </>}
        </div>
      </div>
      <footer className="trade-study-modal-foot"><span role="status">{notice||"指标仅用于图表研究，不会触发交易订单"}</span><button type="button" onClick={onClose}>完成</button></footer>
    </section>
  </div>;
}

export function IndicatorEditor({config,weight,onApply,onClose}:{
  config:IndicatorConfig;weight:number;onApply:(value:IndicatorConfig,weight:number)=>void;onClose:()=>void;
}) {
  const [draft,setDraft]=useState<IndicatorConfig>({...config,parameters:[...config.parameters],colors:[...config.colors]});
  const [height,setHeight]=useState(weight);
  const [tab,setTab]=useState<"inputs"|"style"|"visibility">("inputs");
  const error=indicatorError(draft);
  useEffect(()=>{const handler=(event:KeyboardEvent)=>{if(event.key==="Escape")onClose();};window.addEventListener("keydown",handler);return()=>window.removeEventListener("keydown",handler);},[onClose]);
  const separately=isSeparate(draft);
  return <div className="trade-study-modal-backdrop" onMouseDown={event=>{if(event.target===event.currentTarget)onClose();}}>
    <section className="trade-study-modal trade-study-editor" role="dialog" aria-modal="true" aria-label={`设置 ${draft.kind}`}>
      <header className="trade-study-modal-title"><div><h2>{draft.kind} 指标设置</h2><p>{indicatorCatalog[draft.kind].name}</p></div><button type="button" aria-label="关闭指标设置" onClick={onClose}>×</button></header>
      <div className="trade-study-editor-tabs" role="tablist" aria-label="指标设置选项">{([["inputs","参数"],["style","样式"],["visibility","可见性"]] as const).map(([key,label])=><button key={key} type="button" role="tab" aria-selected={tab===key} className={tab===key?"active":""} onClick={()=>setTab(key)}>{label}</button>)}</div>
      <form onSubmit={event=>{event.preventDefault();if(!error)onApply(draft,height);}}>
        <div className="trade-study-editor-content">
          {tab==="inputs"&&<div className="trade-study-editor-grid">
            {indicatorCatalog[draft.kind].parameters.map((field,i)=><label key={field.label}>{field.label}<input type="number" min={field.min} max={field.max} step={field.step} value={Number.isFinite(draft.parameters[i])?draft.parameters[i]:""} onChange={event=>setDraft({...draft,parameters:draft.parameters.map((v,n)=>n===i?event.target.value===""?NaN:Number(event.target.value):v)})}/></label>)}
            {draft.kind==="CUSTOM"&&<label className="trade-study-full">自定义公式<input type="text" maxLength={160} value={draft.formula??""} onChange={event=>setDraft({...draft,formula:event.target.value})} placeholder="EMA(close, 9) - EMA(close, 21)"/><small>可用 open/high/low/close/volume/hl2/hlc3/ohlc4，SMA、EMA、ROC、ABS 和四则运算。不执行 JavaScript。</small></label>}
            {draft.kind!=="VOL"&&!isMarketOverlay(draft.kind)&&draft.kind!=="SIGNAL"&&draft.kind!=="CUSTOM"&&<label>价格来源<select value={draft.source} onChange={event=>setDraft({...draft,source:event.target.value as IndicatorConfig["source"]})}>{Object.entries(priceSources).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>}
            {draft.kind==="CUSTOM"&&<label>绘制位置<select value={draft.pane??"main"} onChange={event=>setDraft({...draft,pane:event.target.value as "main"|"separate"})}><option value="main">主图叠加</option><option value="separate">独立副图</option></select></label>}
            {separately&&<label className="trade-study-full">副图相对高度：{height}<input type="range" min={1} max={6} step={1} value={height} onChange={event=>setHeight(Number(event.target.value))}/></label>}
            {draft.kind==="SIGNAL"&&<p className="trade-study-note trade-study-full">均线交叉只在图表中标记金叉/死叉，不代表任何策略已执行，更不会发出交易订单。</p>}
          </div>}
          {tab==="style"&&<div className="trade-study-editor-grid">
            {indicatorCatalog[draft.kind].colors.map((label,i)=><label key={label}>{label}颜色<input type="color" value={draft.colors[i]} onChange={event=>setDraft({...draft,colors:draft.colors.map((v,n)=>n===i?event.target.value:v)})}/></label>)}
            {!["VOL","SIGNAL"].includes(draft.kind)&&!isMarketOverlay(draft.kind)&&<><label>线宽<select value={draft.width} onChange={event=>setDraft({...draft,width:Number(event.target.value) as IndicatorConfig["width"]})}>{[1,2,3,4].map(w=><option key={w} value={w}>{w} px</option>)}</select></label><label>线型<select value={draft.style} onChange={event=>setDraft({...draft,style:Number(event.target.value) as IndicatorConfig["style"]})}><option value={0}>实线</option><option value={1}>点线</option><option value={2}>虚线</option></select></label></>}
          </div>}
          {tab==="visibility"&&<div className="trade-study-visibility">
            <label><input type="checkbox" checked={draft.visible} onChange={event=>setDraft({...draft,visible:event.target.checked})}/> 在图表上显示指标</label>
            <p>选择在哪些 K 线周期显示。全部不选表示所有周期可见。</p>
            <div>{knownStudyIntervals.map(period=><label key={period}><input type="checkbox" checked={(draft.visibleIntervals??[]).includes(period)} onChange={event=>{const current=draft.visibleIntervals??[];setDraft({...draft,visibleIntervals:event.target.checked?[...current,period]:current.filter(v=>v!==period)});}}/>{period}</label>)}</div>
            <button type="button" onClick={()=>setDraft({...draft,visibleIntervals:[]})}>全部周期可见</button>
          </div>}
          {error&&<p role="alert" className="trade-study-error">{error}</p>}
        </div>
        <footer className="trade-study-modal-foot"><span>更改只影响图表显示与本地偏好</span><div><button type="button" onClick={onClose}>取消</button><button type="submit" className="primary" disabled={!!error}>应用</button></div></footer>
      </form>
    </section>
  </div>;
}
