export function sma(values: number[], period: number): (number | null)[] {
  let sum = 0;
  return values.map((value, i) => {sum += value; if (i >= period) sum -= values[i-period]; return i >= period-1 ? sum / period : null;});
}
export function ema(values: number[], period: number): (number | null)[] {
  let previous = 0;
  return values.map((value, i) => {
    if (i < period) previous += value / period;
    else previous = value * (2 / (period+1)) + previous * (1-2/(period+1));
    return i >= period-1 ? previous : null;
  });
}
export function bollinger(values: number[], period = 20, multiplier = 2) {
  const middle = sma(values, period);
  return middle.map((mean, i) => {
    if (mean === null) return null;
    const deviation = Math.sqrt(values.slice(i-period+1, i+1).reduce((sum, value) => sum+(value-mean)**2, 0)/period);
    return {middle: mean, upper: mean+multiplier*deviation, lower: mean-multiplier*deviation};
  });
}
export function rsi(values: number[], period = 14): (number | null)[] {
  let gain = 0, loss = 0;
  return values.map((value, i) => {
    if (i === 0) return null;
    const delta = value-values[i-1];
    if (i <= period) {gain += Math.max(delta,0)/period; loss += Math.max(-delta,0)/period;}
    else {gain = (gain*(period-1)+Math.max(delta,0))/period; loss = (loss*(period-1)+Math.max(-delta,0))/period;}
    return i < period ? null : loss === 0 ? gain === 0 ? 50 : 100 : 100-100/(1+gain/loss);
  });
}

export const indicatorCatalog = {
  MA: {name:"简单移动平均线", parameters:[{label:"周期",value:7,min:1,max:500,step:1}], colors:["均线"]},
  EMA: {name:"指数移动平均线", parameters:[{label:"周期",value:7,min:1,max:500,step:1}], colors:["均线"]},
  BOLL: {name:"布林带", parameters:[{label:"周期",value:20,min:2,max:500,step:1},{label:"标准差倍数",value:2,min:0.1,max:10,step:0.1}], colors:["中轨","上轨","下轨"]},
  RSI: {name:"相对强弱指标", parameters:[{label:"周期",value:14,min:1,max:500,step:1},{label:"超买线",value:70,min:1,max:100,step:1},{label:"超卖线",value:30,min:0,max:99,step:1}], colors:["RSI","超买线","超卖线"]},
  MACD: {name:"平滑异同移动平均线", parameters:[{label:"快线周期",value:12,min:1,max:499,step:1},{label:"慢线周期",value:26,min:2,max:500,step:1},{label:"信号周期",value:9,min:1,max:500,step:1}], colors:["DIF","DEA","上涨柱","下跌柱"]},
  VOL: {name:"成交量", parameters:[], colors:["上涨量","下跌量"]},
} as const;
export type IndicatorKind = keyof typeof indicatorCatalog;
export const priceSources = {close:"收盘价",open:"开盘价",high:"最高价",low:"最低价",hl2:"(高+低)/2",hlc3:"(高+低+收)/3",ohlc4:"(开+高+低+收)/4"} as const;
export type PriceSource = keyof typeof priceSources;
export type IndicatorConfig = {id:string;kind:IndicatorKind;visible:boolean;parameters:number[];source:PriceSource;colors:string[];width:1|2|3|4;style:0|1|2};
export const maxIndicators = 24;
export function newIndicator(kind:IndicatorKind,id:string):IndicatorConfig {
  const colors=kind==="VOL"?["#2ebd85","#f6465d"]:kind==="MACD"?["#e5b83c","#d94ab1","#2ebd85","#f6465d"]:kind==="RSI"?["#9063b5","#848e9c","#848e9c"]:["#e5b83c","#d94ab1","#9063b5"];
  return {id,kind,visible:true,parameters:indicatorCatalog[kind].parameters.map(p=>p.value),source:"close",colors:colors.slice(0,indicatorCatalog[kind].colors.length),width:1,style:0};
}
export function defaultIndicators():IndicatorConfig[] {
  return [7,25,99].map((period,i)=>({...newIndicator("MA",`ma-${period}`),parameters:[period],colors:[["#e5b83c","#d94ab1","#9063b5"][i]]})).concat([newIndicator("VOL","volume")]);
}
export function indicatorError(config:IndicatorConfig):string {
  const definition=indicatorCatalog[config.kind];
  if(config.parameters.length!==definition.parameters.length)return "指标参数不完整。";
  for(let i=0;i<definition.parameters.length;i++){
    const field=definition.parameters[i],value=config.parameters[i];
    if(!Number.isFinite(value)||value<field.min||value>field.max||field.step===1&&!Number.isInteger(value))return `${field.label}须为 ${field.min}～${field.max}${field.step===1?" 的整数":" 之间的数值"}。`;
  }
  if(config.kind==="MACD"&&config.parameters[0]>=config.parameters[1])return "MACD 快线周期必须小于慢线周期。";
  if(config.kind==="RSI"&&config.parameters[2]>=config.parameters[1])return "RSI 超卖线必须低于超买线。";
  if(![1,2,3,4].includes(config.width)||![0,1,2].includes(config.style)||!Object.hasOwn(priceSources,config.source))return "指标样式或价格来源无效。";
  if(config.colors.length!==definition.colors.length||config.colors.some(c=>!/^#[\da-f]{6}$/i.test(c)))return "请选择有效的指标颜色。";
  return "";
}
export function restoreIndicators(saved:unknown,legacy:unknown={}):IndicatorConfig[] {
  // Read each stored preference independently: invalid display options must not
  // discard valid custom indicators, and reading must never overwrite storage.
  if(typeof saved==="string") {try {saved=JSON.parse(saved);} catch {saved=null;}}
  if(typeof legacy==="string") {try {legacy=JSON.parse(legacy);} catch {legacy={};}}
  const options=legacy&&typeof legacy==="object"&&!Array.isArray(legacy)?legacy as Record<string,unknown>:{};
  if(Array.isArray(saved)&&saved.length<=maxIndicators){
    const ids=new Set<string>();
    const valid=saved.every(value=>{
      if(!value||typeof value!=="object"||typeof value.id!=="string"||value.id.length>80||ids.has(value.id)||typeof value.visible!=="boolean"||!Object.hasOwn(indicatorCatalog,value.kind)||!Array.isArray(value.parameters)||!Array.isArray(value.colors)||value.colors.some((c:unknown)=>typeof c!=="string")||typeof value.source!=="string")return false;
      ids.add(value.id);return !indicatorError(value);
    });
    if(valid)return saved;
  }
  const rows:IndicatorConfig[]=[];
  for(const kind of Object.keys(indicatorCatalog) as IndicatorKind[]){
    if(!(typeof options[kind]==="boolean"?options[kind]:kind==="MA"||kind==="VOL"))continue;
    if(kind==="MA"||kind==="EMA") [7,25,99].forEach((p,i)=>rows.push({...newIndicator(kind,`${kind}-${p}`),parameters:[p],colors:[["#e5b83c","#d94ab1","#9063b5"][i]]}));
    else rows.push(newIndicator(kind,kind));
  }
  return rows;
}

type IndicatorCandle = {time:number;open:string;high:string;low:string;close:string;volume:string};
export type IndicatorPlot = {key:string;kind:"line"|"histogram";title:string;pane:number;values:(number|null)[];color:string;width:1|2|3|4;style:0|1|2;barColors?:string[];volume?:boolean};
export function indicatorPlots(rows:IndicatorCandle[],configs:IndicatorConfig[]):IndicatorPlot[] {
  const plots:IndicatorPlot[]=[];let pane=1;
  for(const config of configs){
    if(!config.visible)continue;
    const {kind,parameters:p,colors}=config;
    const values=rows.map(r=>{const o=Number(r.open),h=Number(r.high),l=Number(r.low),c=Number(r.close);return config.source==="hl2"?(h+l)/2:config.source==="hlc3"?(h+l+c)/3:config.source==="ohlc4"?(o+h+l+c)/4:Number(r[config.source]);});
    const target=kind==="RSI"||kind==="MACD"||kind==="VOL"?pane++:0;
    function add(key:string,title:string,values:(number|null)[],color:string,type:"line"|"histogram"="line",barColors?:string[]){plots.push({key:`${config.id}:${key}`,kind:type,title,pane:target,values,color,width:config.width,style:config.style,barColors,volume:kind==="VOL"});}
    if(kind==="MA"||kind==="EMA")add("line",`${kind}(${p[0]})`,kind==="MA"?sma(values,p[0]):ema(values,p[0]),colors[0]);
    if(kind==="BOLL"){
      const bands=bollinger(values,p[0],p[1]);
      (["middle","upper","lower"] as const).forEach((key,i)=>add(key,`BOLL(${p.join(",")}) ${key}`,bands.map(b=>b?.[key]??null),colors[i]));
    }
    if(kind==="RSI"){
      const result=rsi(values,p[0]);add("rsi",`RSI(${p[0]})`,result,colors[0]);
      add("upper",`超买(${p[1]})`,result.map(v=>v===null?null:p[1]),colors[1]);add("lower",`超卖(${p[2]})`,result.map(v=>v===null?null:p[2]),colors[2]);
    }
    if(kind==="MACD"){
      const fast=ema(values,p[0]),slow=ema(values,p[1]),diff=values.map((_,i)=>fast[i]!==null&&slow[i]!==null?fast[i]!-slow[i]!:null);
      const signal=ema(diff.filter((v):v is number=>v!==null),p[2]);let i=0;const dea=diff.map(v=>v===null?null:signal[i++]);
      add("dif",`DIF(${p[0]},${p[1]})`,diff,colors[0]);add("dea",`DEA(${p[2]})`,dea,colors[1]);
      const histogram=diff.map((d,i)=>d===null||dea[i]===null?null:2*(d-dea[i]!));
      add("hist","MACD",histogram,colors[2],"histogram",histogram.map(v=>v!==null&&v>=0?colors[2]:colors[3]));
    }
    if(kind==="VOL")add("volume","VOL",rows.map(r=>Number(r.volume)),colors[0],"histogram",rows.map(r=>Number(r.close)>=Number(r.open)?colors[0]:colors[1]));
  }
  return plots;
}
