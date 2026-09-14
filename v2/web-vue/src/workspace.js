import { reactive } from 'vue';
import { api } from './api';
export const state=reactive({ready:false,cohort:'',cohorts:[],students:[],jobs:[],deliveries:[],config:{},urls:[],health:{}});
export async function refresh(){
  const [students,jobs,deliveries]=await Promise.all([api('students'),api('jobs'),api('deliveries')]);
  Object.assign(state,{students,jobs,deliveries});
}
export async function loadCohorts(){
  const data=await api('cohorts');state.cohort=data.active_cohort;
  state.cohorts=[...new Set([...data.cohorts,...Array.from({length:6},(_,i)=>(new Date().getFullYear()-i)+'级')])].sort().reverse();
}
export async function changeCohort(value){await api('cohort',{cohort:value},'PUT');await loadCohorts();await refresh();}
export async function initialize(){
  const token=new URLSearchParams(location.hash.slice(1)).get('token');
  if(token){
    const r=await fetch('/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});
    if(!r.ok)throw Error('启动令牌无效，请从启动器重新打开工作台');
    history.replaceState(null,'',location.pathname);
  }
  const [health,config,urls]=await Promise.all([api('health'),api('config'),api('engines/hivision/urls')]);
  state.health=health;state.config={...config.defaults,...config.saved};state.urls=urls;
  await loadCohorts();await refresh();state.ready=true;
}
