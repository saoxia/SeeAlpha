import { test } from "node:test";
import assert from "node:assert/strict";
import { computeStudyFormula, parseStudyFormula, studyFormulaError } from "./study-formulas.ts";
import { applyTemplate, emptyStudyWorkspace, restoreStudyWorkspace, saveTemplate } from "./study-workspace.ts";
import { crossoverSignals, sanitizeChartSignals } from "./chart-signals.ts";
import { indicatorError, indicatorPlots, newIndicator, restoreIndicators, studyVisibleForInterval } from "./indicators.ts";

const bars=Array.from({length:7},(_,i)=>({time:i+1,open:String(i+1),high:String(i+2),low:String(i),close:String(i+1),volume:String(i+10)}));

test("custom formulas are parsed with a restricted grammar and retain warmup",()=>{
  assert.equal(studyFormulaError("EMA(close, 2) - EMA(close, 3)"),"");
  assert.equal(computeStudyFormula("SMA(close, 3)",bars)[2],2);
  assert.equal(computeStudyFormula("EMA(close, 2) - EMA(close, 3)",bars)[0],null);
  assert.ok(Math.abs(computeStudyFormula("ROC(close,2)",bars)[4]!-200/3)<1e-10);
  assert.deepEqual(computeStudyFormula("ABS(high-low)",bars),Array(7).fill(2));
  const config={...newIndicator("CUSTOM","formula"),formula:"SMA(close, 3)",pane:"separate" as const};
  assert.equal(indicatorError(config),"");
  assert.equal(indicatorPlots(bars,[config])[0].pane,1);
  assert.deepEqual(restoreIndicators([config]),[config]);
});
test("invalid custom expressions cannot execute JS or produce malformed chart values",()=>{
  for(const value of ["","process.exit(1)","constructor","SMA(close, 0)","EMA(close,501)","(close+1","close;alert(1)","Math.random()"]){
    assert.notEqual(studyFormulaError(value),"",value);
    assert.notEqual(indicatorError({...newIndicator("CUSTOM","x"),formula:value}),"",value);
  }
  assert.throws(()=>parseStudyFormula("eval(close)"));
  assert.deepEqual(computeStudyFormula("close / 0",bars),Array(7).fill(null));
});
test("favorites, templates and pane height restore safely and clone configs",()=>{
  const templateStudies=[newIndicator("MA","first"),{...newIndicator("CUSTOM","second"),formula:"EMA(close, 12)"}];
  let workspace=emptyStudyWorkspace();
  workspace={...workspace,favorites:["MA","CUSTOM"],paneWeights:{first:4}};
  workspace=saveTemplate(workspace,"趋势组合",templateStudies,"template-1");
  const restored=restoreStudyWorkspace(JSON.stringify(workspace));
  assert.deepEqual(restored,workspace);
  const next=applyTemplate(restored.templates[0],kind=>"new-"+kind);
  assert.deepEqual(next.map(x=>x.id),["new-MA","new-CUSTOM"]);
  next[0].colors[0]="#123456";
  assert.notEqual(templateStudies[0].colors[0],next[0].colors[0]);
  assert.deepEqual(restoreStudyWorkspace('{"favorites":["MA","UNKNOWN","MA"],"templates":[{"id":"bad","name":"x","studies":[{"bad":1}]}],"paneWeights":{"first":100}}').favorites,["MA"]);
  assert.equal(restoreStudyWorkspace("{broken").templates.length,0);
});
test("interval-specific visibility remains optional for old saved indicators",()=>{
  const study=newIndicator("EMA","first");
  assert.equal(studyVisibleForInterval(study,"1m"),true);
  assert.equal(studyVisibleForInterval({...study,visibleIntervals:["15m"]},"1m"),false);
  assert.equal(studyVisibleForInterval({...study,visibleIntervals:["15m"]},"15m"),true);
  assert.equal(studyVisibleForInterval({...study,visibleIntervals:[],visible:false},"15m"),false);
  assert.notEqual(indicatorError({...study,visibleIntervals:["oops"]}),"");
});
test("crossover markers are chart-only and external strategy signals are validated",()=>{
  const chartBars=[10,10,10,1,1,1,100,100].map((close,i)=>({time:i+1,close:String(close)}));
  const markers=crossoverSignals(chartBars,2,3);
  assert.ok(markers.some(m=>m.direction==="sell"));
  assert.ok(markers.some(m=>m.direction==="buy"));
  assert.ok(markers.every(m=>m.source==="chart"));
  assert.equal(crossoverSignals(chartBars,3,2).length,0);
  const accepted=sanitizeChartSignals([{id:"a",time:42,direction:"buy",label:"验证信号",source:"strategy"},{id:"a",time:42,direction:"sell",label:"重复",source:"strategy"},{id:"b",time:-2,direction:"buy",label:"非法",source:"strategy"}]);
  assert.deepEqual(accepted,[{id:"a",time:42,direction:"buy",label:"验证信号",source:"strategy"}]);
});
