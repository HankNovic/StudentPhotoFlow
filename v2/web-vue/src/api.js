import { ElMessage } from 'element-plus';
export async function api(path,body,method) {
  const options={method:method||(body===undefined?'GET':'POST')};
  if(body!==undefined){options.body=body instanceof FormData?body:JSON.stringify(body);if(!(body instanceof FormData))options.headers={'Content-Type':'application/json'};}
  const response=await fetch('/api/v1/'+path,options),data=await response.json();
  if(!response.ok)throw Error(data.message||JSON.stringify(data.detail||data));
  return data;
}
export function report(error){if(error!=='cancel'&&error!=='close')ElMessage.error(error.message||String(error));}
export async function run(fn){try{return await fn();}catch(e){report(e);}}
export const art=a=>a?'/api/v1/artifacts/'+a.id:'';
export const ids=text=>text.split(/[\s,，]+/).filter(Boolean);
export function download(name,data,type='application/json'){
  const url=URL.createObjectURL(new Blob([typeof data==='string'?data:JSON.stringify(data,null,2)],{type}));
  const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export const labels={missing:'未采集',pending:'原图待处理',processing:'处理任务未完成',review:'待人工审核',approved:'审核通过待交付',delivered:'已交付',delivered_updated:'已交付后照片变化',machine_rejected:'机器退回',review_rejected:'人工退回',failed:'处理失败'};
