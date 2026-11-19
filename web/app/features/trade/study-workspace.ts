import { indicatorCatalog, indicatorError, maxIndicators, type IndicatorConfig, type IndicatorKind } from "./indicators";

export type StudyTemplate = {id:string;name:string;studies:IndicatorConfig[]};
export type StudyWorkspace = {
  favorites:IndicatorKind[];
  templates:StudyTemplate[];
  paneWeights:Record<string,number>;
};
export const workspaceKey="seealpha.chart.study-workspace.v1";
export const emptyStudyWorkspace=():StudyWorkspace=>({favorites:[],templates:[],paneWeights:{}});
function validStudies(studies:unknown):studies is IndicatorConfig[] {
  if(!Array.isArray(studies)||studies.length>maxIndicators)return false;
  const ids=new Set<string>();
  return studies.every(row=>{
    if(!row||typeof row!=="object"||typeof row.id!=="string"||row.id.length>80||ids.has(row.id)||
      typeof row.visible!=="boolean"||!Object.hasOwn(indicatorCatalog,row.kind)||
      !Array.isArray(row.parameters)||!Array.isArray(row.colors)||typeof row.source!=="string")return false;
    ids.add(row.id);
    return !indicatorError(row);
  });
}
export function restoreStudyWorkspace(source:unknown):StudyWorkspace {
  try {
    const data=typeof source==="string"?JSON.parse(source):source;
    if(!data||typeof data!=="object")return emptyStudyWorkspace();
    const obj=data as Record<string,unknown>;
    const favorites=Array.isArray(obj.favorites)
      ? [...new Set(obj.favorites.filter((kind):kind is IndicatorKind=>typeof kind==="string"&&Object.hasOwn(indicatorCatalog,kind)))].slice(0,30)
      : [];
    const templates=Array.isArray(obj.templates)?obj.templates.filter((t):t is StudyTemplate=>
      !!t&&typeof t==="object"&&typeof t.id==="string"&&t.id.length<=80&&
      typeof t.name==="string"&&t.name.trim().length>0&&t.name.length<=40&&validStudies(t.studies)
    ).slice(0,20):[];
    const paneWeights:Record<string,number>={};
    if(obj.paneWeights&&typeof obj.paneWeights==="object"&&!Array.isArray(obj.paneWeights)){
      for(const [id,value] of Object.entries(obj.paneWeights)) {
        if(id.length<=80&&typeof value==="number"&&Number.isInteger(value)&&value>=1&&value<=6)paneWeights[id]=value;
      }
    }
    return {favorites,templates,paneWeights};
  } catch{return emptyStudyWorkspace();}
}
export function saveTemplate(workspace:StudyWorkspace,name:string,studies:IndicatorConfig[],id:string):StudyWorkspace {
  if(name.trim().length===0||name.trim().length>40||!validStudies(studies)||id.length>80)throw Error("模板名称或指标配置无效");
  const template={id,name:name.trim(),studies:studies.map(study=>({...study,parameters:[...study.parameters],colors:[...study.colors]}))};
  return {...workspace,templates:[...workspace.templates.filter(t=>t.name!==template.name),template].slice(-20)};
}
export function applyTemplate(template:StudyTemplate,nextId:(kind:IndicatorKind)=>string):IndicatorConfig[] {
  if(!validStudies(template.studies))return [];
  return template.studies.map(study=>({...study,id:nextId(study.kind),parameters:[...study.parameters],colors:[...study.colors]}));
}
