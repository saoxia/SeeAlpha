"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { tradingRequest, type Markets } from "@/app/features/trade/api";
import { compactVolume, selectMarkets, type MarketSort } from "@/app/features/trade/market-picker";

const favoritesKey = "seealpha.trade.favorites";
const headings: [MarketSort,string][] = [["symbol","合约"],["last_price","最新价格"],["change_percent","24 小时涨跌"],["funding_rate","资金费率"]];
function percentage(value: string | null, rate = false) {return value === null ? "—" : `${Number(value)*(rate?100:1) >= 0 ? "+" : ""}${(Number(value)*(rate?100:1)).toFixed(rate?4:2)}%`;}
export function MarketPicker({symbol, environment, disabled, onSelect}: {symbol:string;environment:"production"|"sandbox";disabled:boolean;onSelect:(symbol:string)=>void}) {
  const [open,setOpen]=useState(false);
  const [host,setHost]=useState<Element|null>(null);
  const [retry,setRetry]=useState(0);
  const [query,setQuery]=useState("");
  const [quote,setQuote]=useState("");
  const [onlyFavorites,setOnlyFavorites]=useState(false);
  const [favorites,setFavorites]=useState<string[]>([]);
  const [sort,setSort]=useState<MarketSort>("quote_volume");
  const [descending,setDescending]=useState(true);
  const [snapshot,setSnapshot]=useState<Markets|null>(null);
  const [error,setError]=useState("");
  const [loading,setLoading]=useState(false);
  const [now,setNow]=useState(0);
  const [position,setPosition]=useState({top:100,left:16});
  const trigger=useRef<HTMLButtonElement>(null), panel=useRef<HTMLDivElement>(null), search=useRef<HTMLInputElement>(null);
  useEffect(()=>{
    const timer=window.setTimeout(()=>{try {const saved=JSON.parse(localStorage.getItem(favoritesKey)??"[]");if(Array.isArray(saved))setFavorites(saved.filter((v):v is string=>typeof v==="string"&&/^[A-Z0-9]+(?:USDT|USDC)-PERP$/.test(v)));}catch{/* Optional browser-only preference. */}},0);
    return()=>window.clearTimeout(timer);
  },[]);
  useEffect(()=>{
    if(!open)return;
    search.current?.focus();
    const controller=new AbortController();let stopped=false;let timer:number;
    async function load(){
      setLoading(true);
      try{const result=await tradingRequest<Markets>(`/api/trading/markets?environment=${environment}`,{signal:controller.signal});if(!stopped){setSnapshot(result);setError("");}}
      catch(e){if(!stopped)setError(e instanceof Error?e.message:"行情列表读取失败。");}
      finally{if(!stopped){setLoading(false);timer=window.setTimeout(load,15000);}}
    }
    void load();
    const clock=window.setInterval(()=>setNow(Date.now()),1000);
    function dismiss(e:PointerEvent){if(e.target instanceof Node&&!panel.current?.contains(e.target)&&!trigger.current?.contains(e.target))setOpen(false);}
    function escape(e:KeyboardEvent){if(e.key==="Escape"){e.stopPropagation();setOpen(false);trigger.current?.focus();}}
    function resize(){setOpen(false);}
    document.addEventListener("pointerdown",dismiss);document.addEventListener("keydown",escape,true);window.addEventListener("resize",resize);
    return()=>{stopped=true;controller.abort();window.clearTimeout(timer);window.clearInterval(clock);document.removeEventListener("pointerdown",dismiss);document.removeEventListener("keydown",escape,true);window.removeEventListener("resize",resize);};
  },[open,environment,retry]);
  function show(){setHost(trigger.current!.closest(".trade-page")??document.body);const rect=trigger.current!.getBoundingClientRect();setPosition({top:Math.min(rect.bottom+12,Math.max(12,window.innerHeight-360)),left:Math.max(12,Math.min(rect.left,window.innerWidth-772))});setNow(Date.now());setOpen(!open);}
  function favorite(value:string){const next=favorites.includes(value)?favorites.filter(v=>v!==value):[...favorites,value];setFavorites(next);try{localStorage.setItem(favoritesKey,JSON.stringify(next));}catch{/* Browsing remains usable without storage. */}}
  function order(key:MarketSort){if(key===sort)setDescending(!descending);else{setSort(key);setDescending(key!=="symbol");}}
  const data=snapshot?.environment===environment?snapshot:null;
  const rows=selectMarkets(data?.markets??[],query,quote,favorites,onlyFavorites,sort,descending);
  const current=symbol.replace(/-PERP$/,"");
  const stale=Boolean(data&&(now-data.checked_at>=60000||now<data.checked_at));
  const arrow=(key:MarketSort)=>sort===key?(descending?" ▾":" ▴"):" ↕";
  return <div className="trade-symbol-picker"><h1><button ref={trigger} className="trade-symbol-trigger" aria-label="选择交易对" aria-expanded={open} aria-haspopup="dialog" disabled={disabled} onClick={show}><span className="trade-coin-badge">{current.slice(0,1)}</span><span>{current}<small>永续</small></span><span className="trade-symbol-caret">{open?"▴":"▾"}</span></button></h1>
    {open&&host&&createPortal(<div ref={panel} className="trade-market-picker" role="dialog" aria-label="选择永续交易对" style={{top:position.top,left:position.left}}>
      <div className="trade-picker-search"><span aria-hidden="true">⌕</span><input ref={search} aria-label="搜索交易对" value={query} onChange={e=>setQuery(e.target.value)} placeholder="查询交易对"/><button aria-label="关闭交易对列表" onClick={()=>{setOpen(false);trigger.current?.focus();}}>×</button></div>
      <div className="trade-picker-tabs"><button className={onlyFavorites?"active":""} onClick={()=>setOnlyFavorites(true)}>自选</button><button className={!onlyFavorites?"active":""} onClick={()=>setOnlyFavorites(false)}>USDⓈ-M</button><button disabled title="尚未接入 COIN-M 交易">COIN-M</button><span>{environment==="sandbox"?"测试网":"永续合约"}</span></div>
      <div className="trade-picker-filters">{[["","全部"],["USDT","USDT"],["USDC","USDC"]].map(([id,label])=><button aria-pressed={quote===id} className={quote===id?"active":""} key={id} onClick={()=>setQuote(id)}>{label}</button>)}<span>{rows.length} 个合约</span></div>
      <div className="trade-picker-columns">{headings.map(([key,label])=><div key={key}><button onClick={()=>order(key)}>{label}{arrow(key)}</button>{key==="symbol"&&<><span> / </span><button onClick={()=>order("quote_volume")}>成交额{arrow("quote_volume")}</button></>}</div>)}</div>
      {error&&<div className="trade-picker-error" role="status">{error} {data?"· 以下为上次快照":""}<button disabled={loading} onClick={()=>setRetry(v=>v+1)}>重试</button></div>}
      <div className={`trade-picker-list${stale||error?" stale":""}`} aria-label="永续合约行情列表">{rows.map(row=><div className={`trade-picker-row${row.symbol===symbol?" selected":""}`} key={row.symbol}><button className={`trade-picker-star${favorites.includes(row.symbol)?" active":""}`} aria-label={`${favorites.includes(row.symbol)?"取消自选":"加入自选"} ${row.base}${row.quote}`} aria-pressed={favorites.includes(row.symbol)} onClick={()=>favorite(row.symbol)}>{favorites.includes(row.symbol)?"★":"☆"}</button><button className="trade-picker-market" aria-label={`选择 ${row.base}${row.quote} 永续`} disabled={disabled} onClick={()=>{onSelect(row.symbol);setOpen(false);trigger.current?.focus();}}><span className="trade-picker-contract"><span className="trade-coin-badge">{row.base.slice(0,1)}</span><span>{row.base}{row.quote}<em>永续</em><small>{compactVolume(row.quote_volume)} <span>{row.quote}</span></small></span></span><span>{row.last_price===null?"—":Number(row.last_price).toLocaleString("en-US",{maximumFractionDigits:12})}</span><span className={row.change_percent===null?"":Number(row.change_percent)>=0?"trade-green":"trade-red"}>{percentage(row.change_percent)}</span><span>{percentage(row.funding_rate,true)}</span></button></div>)}{!rows.length&&<p className="trade-picker-empty">{loading&&!data?"正在读取合约行情…":error&&!data?"行情暂不可用":onlyFavorites&&!favorites.length?"暂无自选，点击合约旁的星标添加":"未找到匹配的永续合约"}</p>}</div>
      <div className="trade-picker-foot"><span>{data?`${stale?"快照已过期 · ":""}${new Date(data.checked_at).toLocaleTimeString("zh-CN",{hour12:false})} · 每15秒刷新` : "公开行情"}</span><span>成交额按各自结算币种计价</span></div>
    </div>,host)}
  </div>;
}
