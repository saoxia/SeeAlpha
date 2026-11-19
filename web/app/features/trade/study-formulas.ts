/**
 * Small, deterministic chart formula language. No eval, JS execution, network,
 * strategy orders or persistence outside the browser are involved.
 * Examples: EMA(close, 20); EMA(close, 9) - EMA(close, 21).
 */
type Bar = {open:string;high:string;low:string;close:string;volume:string};
type Series = (number|null)[];
type Source = "open"|"high"|"low"|"close"|"volume"|"hl2"|"hlc3"|"ohlc4";
type Expr =
  | {type:"number";value:number}
  | {type:"source";name:Source}
  | {type:"unary";value:Expr}
  | {type:"binary";operator:string;left:Expr;right:Expr}
  | {type:"function";name:"SMA"|"EMA"|"ROC"|"ABS";value:Expr;period?:number};

const sources = new Set<Source>(["open","high","low","close","volume","hl2","hlc3","ohlc4"]);
const MAX_FORMULA_LENGTH = 160;
const MAX_TOKENS = 64;

export function parseStudyFormula(formula:string):Expr {
  if(typeof formula!=="string"||!formula.trim()||formula.length>MAX_FORMULA_LENGTH)throw Error("公式长度必须为 1–160 个字符");
  const tokens:string[]=[];
  const pattern=/\s*([A-Za-z][A-Za-z0-9_]*|\d+(?:\.\d+)?|[()+\-*/,])/y;
  let offset=0;
  while(offset<formula.length) {
    if(/^\s+$/.test(formula.slice(offset)))break;
    pattern.lastIndex=offset;
    const match=pattern.exec(formula);
    if(!match)throw Error("公式含不支持的字符");
    tokens.push(match[1]);offset=pattern.lastIndex;
    if(tokens.length>MAX_TOKENS)throw Error("公式过于复杂");
  }
  let index=0;
  let depth=0;
  const peek=()=>tokens[index];
  const take=()=>tokens[index++];
  const expect=(token:string)=>{if(take()!==token)throw Error("公式括号或参数格式不正确");};
  function primary():Expr {
    if(++depth>12)throw Error("公式嵌套过深");
    try {
      const token=take();
      if(!token)throw Error("公式不完整");
      if(token==="-")return {type:"unary",value:primary()};
      if(token==="+")return primary();
      if(token==="(") {const result=expression();expect(")");return result;}
      if(/^\d/.test(token)) {
        const value=Number(token);
        if(!Number.isFinite(value)||Math.abs(value)>1e9)throw Error("常数超出范围");
        return {type:"number",value};
      }
      if(!/^[A-Za-z]/.test(token))throw Error("公式语法错误");
      const upper=token.toUpperCase();
      if(peek()==="(") {
        take();
        if(!["SMA","EMA","ROC","ABS"].includes(upper))throw Error("未知函数："+token);
        const value=expression();
        let period:number|undefined;
        if(upper!=="ABS") {
          expect(",");
          const raw=take();
          period=Number(raw);
          if(!raw||!/^[0-9]+$/.test(raw)||!Number.isInteger(period)||period<1||period>500)throw Error("周期必须是 1–500 的整数");
        }
        expect(")");
        return {type:"function",name:upper as "SMA"|"EMA"|"ROC"|"ABS",value,period};
      }
      const lower=token.toLowerCase() as Source;
      if(!sources.has(lower))throw Error("未知价格字段："+token);
      return {type:"source",name:lower};
    } finally {depth--;}
  }
  function term():Expr {
    let node=primary();
    while(peek()==="*"||peek()==="/")node={type:"binary",operator:take(),left:node,right:primary()};
    return node;
  }
  function expression():Expr {
    let node=term();
    while(peek()==="+"||peek()==="-")node={type:"binary",operator:take(),left:node,right:term()};
    return node;
  }
  const result=expression();
  if(index!==tokens.length)throw Error("公式末尾存在无效内容");
  return result;
}
export function studyFormulaError(formula:string):string {
  try {parseStudyFormula(formula);return "";} catch(error) {return error instanceof Error?error.message:"无效公式";}
}
export function computeStudyFormula(formula:string,bars:readonly Bar[]):Series {
  const ast=parseStudyFormula(formula);
  const length=bars.length;
  const clean=(n:number):number|null=>Number.isFinite(n)?n:null;
  function evaluate(node:Expr):Series {
    if(node.type==="number")return Array(length).fill(node.value);
    if(node.type==="source")return bars.map(bar=>{
      const open=Number(bar.open),high=Number(bar.high),low=Number(bar.low),close=Number(bar.close);
      const number=node.name==="hl2"?(high+low)/2:node.name==="hlc3"?(high+low+close)/3:node.name==="ohlc4"?(open+high+low+close)/4:Number(bar[node.name]);
      return clean(number);
    });
    if(node.type==="unary")return evaluate(node.value).map(v=>v===null?null:clean(-v));
    if(node.type==="binary") {
      const left=evaluate(node.left),right=evaluate(node.right);
      return left.map((a,i)=>{const b=right[i];if(a===null||b===null)return null;
        return clean(node.operator==="+"?a+b:node.operator==="-"?a-b:node.operator==="*"?a*b:b===0?NaN:a/b);});
    }
    const values=evaluate(node.value);
    if(node.name==="ABS")return values.map(v=>v===null?null:Math.abs(v));
    const period=node.period!;
    if(node.name==="ROC")return values.map((v,i)=>{
      const prev=i>=period?values[i-period]:null;
      return v===null||prev===null||prev===0?null:clean((v-prev)/Math.abs(prev)*100);
    });
    if(node.name==="SMA")return values.map((_,i)=>{
      if(i<period-1)return null;
      const window=values.slice(i-period+1,i+1);
      return window.some(v=>v===null)?null:clean(window.reduce<number>((sum,v)=>sum+(v??0),0)/period);
    });
    let previous:number|null=null;
    let warmup:number[]=[];
    return values.map(value=>{
      if(value===null){warmup=[];previous=null;return null;}
      if(previous===null){
        warmup.push(value);
        if(warmup.length<period)return null;
        previous=warmup.reduce((sum,n)=>sum+n,0)/period;
      } else previous=(value*2)/(period+1)+previous*(1-2/(period+1));
      return clean(previous);
    });
  }
  return evaluate(ast);
}
