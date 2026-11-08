"use client";

import { useState } from "react";
import { requestId } from "@/app/features/trade/api";
import { defaultIndicators, indicatorCatalog, indicatorError, maxIndicators, newIndicator, priceSources, type IndicatorConfig, type IndicatorKind } from "@/app/features/trade/indicators";

export function IndicatorSettings({configs,onChange,onClose,candleCount}:{configs:IndicatorConfig[];onChange:(configs:IndicatorConfig[])=>void;onClose:()=>void;candleCount:number}) {
  const [query,setQuery]=useState("");
  const [draft,setDraft]=useState<IndicatorConfig|null>(null);
  const error=draft?indicatorError(draft):"";
  function edit(config:IndicatorConfig){setDraft(draft?.id===config.id?null:{...config,parameters:[...config.parameters],colors:[...config.colors]});}
  function add(kind:IndicatorKind){const config=newIndicator(kind,`study-${requestId()}`);onChange([...configs,config]);setDraft(config);}
  function move(index:number,direction:number){const next=[...configs];[next[index],next[index+direction]]=[next[index+direction],next[index]];onChange(next);}
  return <section className="trade-chart-menu trade-indicator-menu" aria-label="自定义技术指标"><div className="trade-chart-menu-title"><strong>技术指标</strong><span>{configs.filter(c=>c.visible).length} 个显示 · {configs.length}/{maxIndicators} 组</span><button aria-label="关闭指标设置" onClick={onClose}>×</button></div>
    <input className="trade-indicator-search" aria-label="搜索指标" placeholder="搜索指标名称，例如 MA、布林带" value={query} onChange={e=>setQuery(e.target.value)}/>
    <div className="trade-indicator-library">{(Object.keys(indicatorCatalog) as IndicatorKind[]).filter(kind=>`${kind} ${indicatorCatalog[kind].name}`.toLowerCase().includes(query.trim().toLowerCase())).map(kind=><button key={kind} disabled={configs.length>=maxIndicators} aria-label={`添加 ${kind} 指标`} onClick={()=>add(kind)}><b>＋ {kind}</b><small>{indicatorCatalog[kind].name}</small></button>)}</div>
    <div className="trade-indicator-list">{configs.map((config,index)=>{
      const title=`${config.kind}${config.parameters.length?`(${config.parameters.join(", ")})`:""}`;
      const editing=draft?.id===config.id;
      return <article key={config.id} className={`trade-indicator-card${editing?" editing":""}`}><div className="trade-indicator-card-head"><label><input type="checkbox" aria-label={`显示 ${config.kind} 第${index+1}组`} checked={config.visible} onChange={e=>onChange(configs.map(c=>c.id===config.id?{...c,visible:e.target.checked}:c))}/><span>{title}</span></label><span className="trade-indicator-swatches" aria-hidden="true">{config.colors.map((color,i)=><i key={i} style={{background:color}}/>)}</span><span className="trade-indicator-location">{["MA","EMA","BOLL"].includes(config.kind)?"主图":"副图"}</span><button aria-label={`上移 ${config.kind} 第${index+1}组`} disabled={index===0} onClick={()=>move(index,-1)}>↑</button><button aria-label={`下移 ${config.kind} 第${index+1}组`} disabled={index===configs.length-1} onClick={()=>move(index,1)}>↓</button><button aria-expanded={editing} aria-label={`设置 ${config.kind} 第${index+1}组`} onClick={()=>edit(config)}>设置</button><button aria-label={`删除 ${config.kind} 第${index+1}组`} onClick={()=>{onChange(configs.filter(c=>c.id!==config.id));if(editing)setDraft(null);}}>×</button></div>
        {editing&&draft&&<form className="trade-indicator-editor" onSubmit={e=>{e.preventDefault();if(error)return;onChange(configs.map(c=>c.id===draft.id?{...draft,visible:c.visible}:c));setDraft(null);}}><div className="trade-indicator-fields">{indicatorCatalog[draft.kind].parameters.map((field,i)=><label key={field.label}>{field.label}<input aria-label={`${draft.kind} ${field.label}`} type="number" min={field.min} max={field.max} step={field.step} value={Number.isFinite(draft.parameters[i])?draft.parameters[i]:""} onChange={e=>setDraft({...draft,parameters:draft.parameters.map((v,n)=>n===i?e.target.value===""?NaN:Number(e.target.value):v)})}/></label>)}
          {draft.kind!=="VOL"&&<><label>价格来源<select aria-label={`${draft.kind} 价格来源`} value={draft.source} onChange={e=>setDraft({...draft,source:e.target.value as IndicatorConfig["source"]})}>{Object.entries(priceSources).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label><label>线宽<select aria-label={`${draft.kind} 线宽`} value={draft.width} onChange={e=>setDraft({...draft,width:Number(e.target.value) as IndicatorConfig["width"]})}>{[1,2,3,4].map(width=><option key={width} value={width}>{width} px</option>)}</select></label><label>线型<select aria-label={`${draft.kind} 线型`} value={draft.style} onChange={e=>setDraft({...draft,style:Number(e.target.value) as IndicatorConfig["style"]})}><option value={0}>实线</option><option value={1}>点线</option><option value={2}>虚线</option></select></label></>}
          {indicatorCatalog[draft.kind].colors.map((label,i)=><label className="trade-indicator-color" key={label}>{label}<input aria-label={`${draft.kind} ${label}颜色`} type="color" value={draft.colors[i]} onChange={e=>setDraft({...draft,colors:draft.colors.map((v,n)=>n===i?e.target.value:v)})}/></label>)}</div>
          {error&&<p className="trade-indicator-error" role="alert">{error}</p>}<div className="trade-indicator-apply"><button type="button" onClick={()=>setDraft(null)}>取消</button><button type="submit" disabled={Boolean(error)}>应用</button></div></form>}
      </article>;
    })}{!configs.length&&<p>尚未添加指标，点击上方指标可添加多组。</p>}</div>
    <footer><p>应用后更新图表；可添加多组同类指标，副图可拖动分隔线调整高度。当前载入 {candleCount} 根 K 线，预热不足时不绘制曲线。MACD 柱 = 2 × (DIF − DEA)。</p><button onClick={()=>{onChange(defaultIndicators());setDraft(null);}}>恢复默认指标</button></footer>
  </section>;
}
