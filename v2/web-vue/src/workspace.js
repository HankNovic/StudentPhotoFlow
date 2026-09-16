import { reactive } from 'vue';
import { api,scope } from './api';
export const state=reactive({ready:false,cohort:'',cohortName:'',archived:false,cohorts:[],allCohorts:[],students:[],jobs:[],deliveries:[],config:{},savedConfig:{},urls:[],health:{}});
let refreshRequest=0;
export async function refresh(){
  const cid=state.cohort,request=++refreshRequest;if(!cid){Object.assign(state,{students:[],jobs:[],deliveries:[]});return;}
  const [students,jobs,deliveries]=await Promise.all([api('students',undefined,undefined,cid),api('jobs',undefined,undefined,cid),api('deliveries',undefined,undefined,cid)]);
  if(cid===state.cohort&&request===refreshRequest)Object.assign(state,{students,jobs,deliveries});
}
export async function loadCohorts(){
  const data=await api('cohorts');state.allCohorts=data.cohorts;state.cohorts=data.cohorts.filter(c=>!c.archived);
  const remembered=state.cohort||sessionStorage.getItem('spf-cohort');
  const c=data.cohorts.find(c=>c.id===remembered&&!c.archived)||state.cohorts[0];
  Object.assign(state,{cohort:c?.id||'',cohortName:c?.name||'',archived:c?.archived||false});scope.id=state.cohort;
}
export async function changeCohort(value){
  const c=state.allCohorts.find(c=>c.id===value||c.name===value);
  if(!c)throw Error('届次未登记，请先到系统设置添加');
  Object.assign(state,{students:[],jobs:[],deliveries:[],cohort:c.id,cohortName:c.name,archived:c.archived});scope.id=c.id;sessionStorage.setItem('spf-cohort',c.id);await refresh();
}
export async function initialize(){
  const token=new URLSearchParams(location.hash.slice(1)).get('token');
  if(token){
    const r=await fetch('/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});
    if(!r.ok)throw Error('启动令牌无效，请从启动器重新打开工作台');
    history.replaceState(null,'',location.pathname);
  }
  const [health,config,urls]=await Promise.all([api('health'),api('config'),api('engines/hivision/urls')]);
  state.health=health;state.config={...config.defaults,...config.saved};state.savedConfig={...state.config};state.urls=urls;
  await loadCohorts();await refresh();state.ready=true;
}
