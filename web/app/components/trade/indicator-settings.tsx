"use client";

import { useEffect, useRef, useState } from "react";
import { requestId } from "@/app/features/trade/api";
import { defaultIndicators, indicatorCatalog, indicatorError, maxIndicators, newIndicator, isMarketOverlay, priceSources, type IndicatorConfig, type IndicatorKind } from "@/app/features/trade/indicators";

export function IndicatorSettings({configs,onChange,onClose,candleCount}:{configs:IndicatorConfig[];onChange:(configs:IndicatorConfig[])=>void;onClose:()=>void;candleCount:number}) {
  const [query,setQuery]=useState("");
  const [pickerOpen,setPickerOpen]=useState(false);
  const picker=useRef<HTMLDivElement>(null);
  const trigger=useRef<HTMLButtonElement>(null);
  const search=useRef<HTMLInputElement>(null);
  const [draft,setDraft]=useState<IndicatorConfig|null>(null);
  const error=draft?indicatorError(draft):"";
  const matches=(Object.keys(indicatorCatalog) as IndicatorKind[]).filter(kind=>
    (kind+" "+indicatorCatalog[kind].name).toLowerCase().includes(query.trim().toLowerCase()));
  const unavailable=(kind:IndicatorKind)=>configs.length>=maxIndicators||
    isMarketOverlay(kind)&&configs.some(config=>config.kind===kind);
  useEffect(()=>{
    if(!pickerOpen)return;
    search.current?.focus();
    const dismiss=(event:PointerEvent)=>{if(!picker.current?.contains(event.target as Node))setPickerOpen(false);};
    document.addEventListener("pointerdown",dismiss);
    return ()=>document.removeEventListener("pointerdown",dismiss);
  },[pickerOpen]);
  function edit(config:IndicatorConfig){setDraft(draft?.id===config.id?null:{...config,parameters:[...config.parameters],colors:[...config.colors]});}
  function add(kind:IndicatorKind){if(unavailable(kind))return;const config=newIndicator(kind,`study-${requestId()}`);onChange([...configs,config]);setDraft(config);setQuery("");setPickerOpen(false);}
  function move(index:number,direction:number){const next=[...configs];[next[index],next[index+direction]]=[next[index+direction],next[index]];onChange(next);}
  return <section className="trade-chart-menu trade-indicator-menu" aria-label="自定义技术指标"><div className="trade-chart-menu-title"><strong>技术指标</strong><span>{configs.filter(c=>c.visible).length} 个显示 · {configs.length}/{maxIndicators} 组</span><button aria-label="关闭指标设置" onClick={onClose}>×</button></div>
    <div className="trade-indicator-picker" ref={picker} onBlur={event=>{if(!event.currentTarget.contains(event.relatedTarget))setPickerOpen(false);}}>
      <button ref={trigger} type="button" className="trade-indicator-trigger" aria-label="添加指标" aria-controls="trade-indicator-options" aria-expanded={pickerOpen} onClick={()=>{setQuery("");setPickerOpen(open=>!open);}}>
        <span>＋ 添加指标</span><span aria-hidden="true">⌄</span>
      </button>
      {pickerOpen&&<div className="trade-indicator-dropdown" id="trade-indicator-options">
        <input ref={search} type="search" className="trade-indicator-search" aria-label="搜索指标" placeholder="搜索指标名称或代码，如 EMA、布林带" value={query} onChange={event=>setQuery(event.target.value)} onKeyDown={event=>{
          if(event.key==="Escape"){event.preventDefault();setPickerOpen(false);trigger.current?.focus();}
          if(event.key==="Enter"){const first=matches.find(kind=>!unavailable(kind));if(first){event.preventDefault();add(first);}}
        }}/>
        <div className="trade-indicator-options" role="group" aria-label="可添加的指标">
          {matches.map(kind=><button key={kind} type="button" className="trade-indicator-option" disabled={unavailable(kind)} aria-label={"添加 "+kind+" 指标"} onClick={()=>add(kind)}>
            <b>{kind}</b><span>{indicatorCatalog[kind].name}</span>{isMarketOverlay(kind)&&configs.some(config=>config.kind===kind)&&<small>已添加</small>}
          </button>)}
          {!matches.length&&<p role="status">没有找到匹配的指标。</p>}
          {configs.length>=maxIndicators&&<p role="status">已达到 {maxIndicators} 组指标上限，请先删除现有指标。</p>}
        </div>
      </div>}
    </div>
    <div className="trade-indicator-list">{configs.map((config,index)=>{
      const title=`${config.kind}${config.parameters.length?`(${config.parameters.join(", ")})`:""}`;
      const editing=draft?.id===config.id;
      return <article key={config.id} className={`trade-indicator-card${editing?" editing":""}`}><div className="trade-indicator-card-head"><label><input type="checkbox" aria-label={`显示 ${config.kind} 第${index+1}组`} checked={config.visible} onChange={e=>onChange(configs.map(c=>c.id===config.id?{...c,visible:e.target.checked}:c))}/><span>{title}</span></label><span className="trade-indicator-swatches" aria-hidden="true">{config.colors.map((color,i)=><i key={i} style={{background:color}}/>)}</span><span className="trade-indicator-location">{["MA","EMA","BOLL"].includes(config.kind)||isMarketOverlay(config.kind)?"主图":"副图"}</span><button aria-label={`上移 ${config.kind} 第${index+1}组`} disabled={index===0} onClick={()=>move(index,-1)}>↑</button><button aria-label={`下移 ${config.kind} 第${index+1}组`} disabled={index===configs.length-1} onClick={()=>move(index,1)}>↓</button><button aria-expanded={editing} aria-label={`设置 ${config.kind} 第${index+1}组`} onClick={()=>edit(config)}>设置</button><button aria-label={`删除 ${config.kind} 第${index+1}组`} onClick={()=>{onChange(configs.filter(c=>c.id!==config.id));if(editing)setDraft(null);}}>×</button></div>
        {editing&&draft&&<form className="trade-indicator-editor" onSubmit={e=>{e.preventDefault();if(error)return;onChange(configs.map(c=>c.id===draft.id?{...draft,visible:c.visible}:c));setDraft(null);}}><div className="trade-indicator-fields">{indicatorCatalog[draft.kind].parameters.map((field,i)=><label key={field.label}>{field.label}<input aria-label={`${draft.kind} ${field.label}`} type="number" min={field.min} max={field.max} step={field.step} value={Number.isFinite(draft.parameters[i])?draft.parameters[i]:""} onChange={e=>setDraft({...draft,parameters:draft.parameters.map((v,n)=>n===i?e.target.value===""?NaN:Number(e.target.value):v)})}/></label>)}
          {draft.kind!=="VOL"&&!isMarketOverlay(draft.kind)&&<><label>价格来源<select aria-label={`${draft.kind} 价格来源`} value={draft.source} onChange={e=>setDraft({...draft,source:e.target.value as IndicatorConfig["source"]})}>{Object.entries(priceSources).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label><label>线宽<select aria-label={`${draft.kind} 线宽`} value={draft.width} onChange={e=>setDraft({...draft,width:Number(e.target.value) as IndicatorConfig["width"]})}>{[1,2,3,4].map(width=><option key={width} value={width}>{width} px</option>)}</select></label><label>线型<select aria-label={`${draft.kind} 线型`} value={draft.style} onChange={e=>setDraft({...draft,style:Number(e.target.value) as IndicatorConfig["style"]})}><option value={0}>实线</option><option value={1}>点线</option><option value={2}>虚线</option></select></label></>}
          {indicatorCatalog[draft.kind].colors.map((label,i)=><label className="trade-indicator-color" key={label}>{label}<input aria-label={`${draft.kind} ${label}颜色`} type="color" value={draft.colors[i]} onChange={e=>setDraft({...draft,colors:draft.colors.map((v,n)=>n===i?e.target.value:v)})}/></label>)}</div>
          {error&&<p className="trade-indicator-error" role="alert">{error}</p>}<div className="trade-indicator-apply"><button type="button" onClick={()=>setDraft(null)}>取消</button><button type="submit" disabled={Boolean(error)}>应用</button></div></form>}
      </article>;
    })}{!configs.length&&<p>尚未添加指标，请使用上方“添加指标”下拉框。</p>}</div>
    <footer><p>应用后更新图表；常规指标可重复添加，VP/BOOK/LIQ 各限一组。VP 由 K 线 OHLCV 近似计算；BOOK 仅显示页面打开后收到的真实 20 档盘口快照（非历史重建）；LIQ 必须具备独立强平估算模型数据，否则不绘制虚构强平水平。当前载入 {candleCount} 根 K 线，预热不足时不绘制曲线。MACD 柱 = 2 × (DIF − DEA)。</p><button onClick={()=>{onChange(defaultIndicators());setDraft(null);}}>恢复默认指标</button></footer>
  </section>;
}
