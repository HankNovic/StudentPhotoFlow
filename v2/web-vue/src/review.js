// Presentation only: use the persisted student, task and delivery fields.
export const taskLabels={running:'运行中',pausing:'正在暂停',paused:'已暂停',cancelling:'正在安全结束',cancelled:'已手动结束',interrupted:'系统中断，需核对后恢复',completed:'已结束',queued:'等待执行'};
export function reviewState(detail,jobs=[],batches=[],archived=false){
  const source=detail?.sources.at(-1),result=detail?.results.filter(r=>r.source_id===source?.id).at(-1);
  const active=jobs.find(j=>['running','pausing','paused','cancelling','queued'].includes(j.status));
  const uncertain=jobs.some(j=>j.uncertain?.[detail?.id]);
  const prepared=batches.find(b=>b.status==='prepared');
  const delivered=!!detail?.delivered&&!detail?.replacement;
  const lockReason=archived?'当前届次已归档，只读查看；请先在系统设置恢复。':active?`关联任务${taskLabels[active.status]}；请到任务进度页查看或继续。该任务结束前暂不能修改、审核或交付。`:uncertain?'此学生有结果不确定的中断项目，请先到任务进度页核对。':'';
  const editable=!!detail&&!lockReason&&!prepared&&!delivered;
  const approved=detail?.status==='approved';
  const formal=!!result?.artifact&&['success','warning'].includes(result.status);
  return {source,result,prepared,delivered,lockReason,
    upload:editable&&!approved,process:editable&&!approved&&!!source,
    replace:!!detail&&delivered&&!lockReason&&!prepared,
    approve:editable&&formal&&detail.status==='review',
    reject:editable&&formal&&detail.status==='review',
    undo:editable&&!!result?.artifact&&['approved','rejected'].includes(result.review),
    deliver:editable&&approved,
  };
}
export function newRequestId(crypto=globalThis.crypto){
  if(typeof crypto?.randomUUID==='function')return crypto.randomUUID();
  if(typeof crypto?.getRandomValues!=='function')throw Error('浏览器不支持安全随机数，无法生成处理请求编号；请使用支持的浏览器或安全访问地址。');
  const bytes=crypto.getRandomValues(new Uint8Array(16));
  bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
  const h=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
  return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
}
