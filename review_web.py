"""Shared student search and a focused manual review page (no third-party assets)."""

SHARED_STYLE = """
<style>
.spf-search{display:flex;flex-wrap:wrap;gap:10px;align-items:center;padding:14px 24px;background:#fff;border-bottom:1px solid #dbe3ed;color:#172033}
.spf-search input,.spf-search button,.spf-search a{font:inherit;border:1px solid #b8c7da;border-radius:8px;padding:9px 12px}
.spf-search input{min-width:230px}.spf-search button{background:#175cd3;color:#fff;cursor:pointer}
.spf-search a{color:#175cd3;text-decoration:none}.spf-search small{color:#52657e}
#spf-detail{width:min(1100px,92vw);max-height:88vh;border:1px solid #dbe3ed;border-radius:14px;padding:20px;color:#172033}
#spf-detail::backdrop{background:#13263c88}.spf-detail-steps{display:flex;gap:14px;overflow:auto;padding:12px 0}
.spf-detail-steps figure{flex:0 0 220px;margin:0;border:1px solid #dbe3ed;border-radius:8px;overflow:hidden}
.spf-detail-steps img{width:100%;height:290px;object-fit:contain;background:#eef3f8}
.spf-detail-steps figcaption{padding:8px;font-size:13px;display:block}.spf-detail-steps small{display:block;color:#5d6879}
.spf-review-badge{display:inline-block;margin:5px 0;padding:4px 9px;border-radius:99px;background:#eaf2fe;color:#175cd3;font-size:13px}
</style>
"""


def search_markup(mode: str) -> str:
    return f"""<section class="spf-search" id="spf-shared-search" data-mode="{mode}">
<form id="spf-search-form"><input id="spf-student-id" aria-label="搜索全年级学号" placeholder="输入完整学号，查全年级流程结果" autocomplete="off"><button type="submit">搜索学号</button></form>
<a id="spf-review-link" href="#" target="_blank" rel="noopener">打开逐张审核</a>
<small id="spf-gate">正在读取全年级名单与归档状态…</small></section>
<dialog id="spf-detail"><button id="spf-close-detail" type="button">关闭详情</button><div id="spf-detail-body"></div></dialog>"""


SHARED_SCRIPT = r"""
<script>
(() => {
 const parts=location.pathname.split('/'); const token=parts[1]==='reports'?parts[2]:'';
 const labels={delivered:'历史已交付 · 已锁定',approved:'已通过 · 已归档',rejected:'审核不通过',skipped:'已跳过',pending:'待审核',stale:'图片已变化 · 需重新审核'};
 const fileUrl=(path)=>'/reports/'+token+'/output/'+String(path).split('/').map(encodeURIComponent).join('/');
 async function get(action, values={}) {
   if(!token || location.protocol!=='http:') throw new Error('请保持主程序打开，并从主程序“查看最新批次”进入网页');
   const query=new URLSearchParams({report_token:token,action,...values});
   const response=await fetch('/api/review?'+query,{cache:'no-store'}); const data=await response.json();
   if(!response.ok) throw new Error(data.message || '请求失败'); return data;
 }
 async function post(values) {
   const response=await fetch('/api/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({report_token:token,...values})});
   const data=await response.json(); if(!response.ok) throw new Error(data.message || '保存失败'); return data;
 }
 function detailView(detail, container) {
   container.replaceChildren();
   const title=document.createElement('h2');title.textContent=detail.student_id+' · '+(labels[detail.status]||detail.status);container.append(title);
   const note=document.createElement('p');note.textContent=(detail.in_roster?'全年级名单内':'不在全年级名单中')+'｜'+detail.processing_status+'｜'+detail.message;container.append(note);
   const strip=document.createElement('div');strip.className='spf-detail-steps';container.append(strip);
   const pictures=[{label:'原始图片',file:detail.original_file},...(detail.steps||[]),{label:'最终结果',file:detail.result_file},{label:'归档副本',file:detail.archive_file}];
   pictures.filter(item=>item.file).forEach(item=>{
     const fig=document.createElement('figure'),img=document.createElement('img'),cap=document.createElement('figcaption'),small=document.createElement('small');
     img.loading='lazy';img.src=fileUrl(item.file);img.alt=item.label||'流程图片';cap.textContent=(item.label||item.code||'流程结果')+(item.status?' · '+item.status:'');
     small.textContent=item.detail||'';cap.append(small);fig.append(img,cap);strip.append(fig);
   });
   if(!detail.exists){const note=document.createElement('p');note.textContent=detail.in_roster?'该学号在全年级名单中，但尚未导出照片。':'未找到此学号，请检查输入。';container.append(note);}
 }
 async function refreshGate() {
   try {
     const data=await get('queue',{filter:'all'});const c=data.counts;
     document.getElementById('spf-gate').textContent=`全年级 ${c.total} 人｜已交付锁定 ${c.delivered||0}｜剩余待交付 ${c.total-(c.delivered||0)}｜`+(data.enabled?`归档 ${c.approved}｜待处理 ${c.missing}`:'审核未启用；已交付锁定仍有效');
     const link=document.getElementById('spf-review-link');link.href=data.enabled?'/reports/'+token+'/review.html':'#';link.dataset.enabled=String(data.enabled);
     document.querySelectorAll('article.card').forEach(card=>{
       let badge=card.querySelector('.spf-review-badge');if(!badge){badge=document.createElement('span');badge.className='spf-review-badge';(card.querySelector('.meta')||card).append(badge);}
       const status=(data.statuses||{})[card.dataset.studentId];badge.textContent=labels[status]||'未列入审核名单';
     });
     return data;
   } catch(error){document.getElementById('spf-gate').textContent=error.message;return null;}
 }
 async function openStudent(sid){
   document.getElementById('spf-student-id').value=sid;
   if(document.getElementById('spf-shared-search').dataset.mode==='review'){await window.loadReviewStudent(sid);}
   else{const detail=await get('student',{student_id:sid});detailView(detail,document.getElementById('spf-detail-body'));document.getElementById('spf-detail').showModal();}
 }
 window.SPF={get,post,fileUrl,detailView,refreshGate,labels,openStudent};
 const form=document.getElementById('spf-search-form');
 form.addEventListener('submit',async event=>{
   event.preventDefault();const sid=document.getElementById('spf-student-id').value.trim();if(!sid)return;
   try{await openStudent(sid);}
   catch(error){alert(error.message);}
 });
 document.getElementById('spf-close-detail').onclick=()=>document.getElementById('spf-detail').close();
 document.getElementById('spf-review-link').onclick=event=>{if(event.currentTarget.dataset.enabled!=='true'){event.preventDefault();alert('请先在主程序校验保存全年级名单，并启用审核归档。');}};
 window.addEventListener('focus',refreshGate);refreshGate();
})();
</script>
"""


OVERVIEW_STYLE = """
<style>
.spf-overview{margin:18px 24px;padding:20px;background:white;border:1px solid #dbe3ed;border-radius:14px;color:#172033}
.spf-overview h2{font-size:20px;margin:0}.spf-overview .spf-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:12px 0}
.spf-overview button,.spf-overview select,.spf-overview input{font:inherit;padding:8px 10px;border:1px solid #b9c9db;background:white;border-radius:7px;color:#17365d}
.spf-overview button{cursor:pointer}.spf-overview button:disabled{opacity:.45;cursor:not-allowed}.spf-overview button:focus-visible,.spf-overview input:focus-visible{outline:3px solid #84adff}
.spf-overview p,.spf-overview small{font-size:13px;color:#52657e}.spf-overview #spf-overview-note{white-space:pre-wrap}
.spf-overview #spf-overview-note.error{color:#b42318}.spf-overview #spf-overview-note.warning{color:#956000}
.spf-stats{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:14px 0}
.spf-stats button{text-align:left;background:#f5f8fc;border-color:#e1e8f1;padding:12px;min-height:76px}
.spf-stats button:nth-child(2){background:#ecfdf3}.spf-stats button:nth-child(3){background:#fffaeb}
.spf-stats button[aria-pressed=true]{outline:2px solid #175cd3}.spf-stats strong{display:block;font-size:26px;line-height:1.3;color:#172033}
.spf-table-wrap{max-height:410px;overflow:auto;border:1px solid #e1e8f1;border-radius:8px}.spf-overview table{width:100%;border-collapse:collapse;min-width:720px}
.spf-overview th{position:sticky;top:0;background:#f1f5fa;text-align:left;font-size:13px;z-index:1}.spf-overview th,.spf-overview td{padding:10px;border-bottom:1px solid #e8eef4;vertical-align:top}
.spf-overview td{font-size:13px}.spf-overview td small{display:block;font-size:12px}.spf-overview td:first-child button{padding:0;border:0;background:none;color:#175cd3;font-weight:600;white-space:nowrap}
.spf-overview .spf-reason{max-width:380px;min-width:180px;overflow-wrap:anywhere;white-space:pre-wrap}.spf-overview .spf-batch{overflow-wrap:anywhere;max-width:160px}
.spf-overview textarea{width:100%;box-sizing:border-box;min-height:120px;font:14px/1.6 Consolas,monospace;border:1px solid #c8d5e5;border-radius:7px;margin-top:8px;padding:10px}
@media(max-width:1000px){.spf-stats{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:700px){.spf-overview{margin:12px;padding:14px}.spf-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.spf-overview #spf-roster-query{width:100%}}
</style>
"""


def overview_markup() -> str:
    return """<section class="spf-overview" id="spf-overview" aria-label="全年级综合统计">
<div class="spf-bar"><h2>全年级综合统计与学号</h2><button id="spf-overview-refresh">刷新全量统计</button><small id="spf-overview-time"></small></div>
<p>跨全部历史批次，以已保存的全年级名单为准。全年级 = 已交付锁定 + 剩余待交付；剩余待交付 = 有效已归档（未锁定）+ 未通过（未锁定）。其余分类可交叉。已交付不等于当前新照片已审核通过。</p>
<p id="spf-overview-note" role="status">正在核对全年级审核、图片版本与历史处理记录…</p>
<details id="spf-warning-details" hidden><summary>查看统计提示</summary><ul id="spf-warning-list"></ul></details>
<div class="spf-stats" id="spf-stats"></div>
<div class="spf-bar"><label>学号范围 <select id="spf-roster-group" aria-label="综合名单范围"></select></label>
<input id="spf-roster-query" aria-label="筛选综合名单" placeholder="筛选学号、分类或错误原因" autocomplete="off">
<button id="spf-roster-json" disabled>导出筛选 JSON</button><button id="spf-roster-txt" disabled>导出筛选学号 TXT</button><button id="spf-roster-csv" disabled>导出筛选明细 CSV</button></div>
<p id="spf-roster-count">点击统计卡片查看相应学号；点击学号查看详细流程。</p>
<div class="spf-table-wrap"><table><thead><tr><th>学号 / 流程</th><th>分类</th><th>人工审核</th><th>处理状态</th><th>原因 / 版本说明</th><th>处理批次</th></tr></thead><tbody id="spf-roster-body"></tbody></table></div>
<div class="spf-bar"><button id="spf-roster-prev" disabled>上一页</button><span id="spf-roster-page">0 / 0</span><button id="spf-roster-next" disabled>下一页</button><small>每页 20 人；JSON / TXT / CSV 包含所有符合筛选的学号，不仅本页。</small></div>
<details><summary>展开当前筛选的全部学号（可复制）</summary><textarea id="spf-roster-ids" aria-label="筛选后的全部学号" readonly></textarea></details>
<p>历史已交付名单：主程序“已交付锁定…”导入，手动解锁需填写原因。照片交付：主程序“审核结果导出…”。已锁定者在初次、新增交付中均跳过。</p>
</section>"""


OVERVIEW_SCRIPT = r"""
<script>
(() => {
 const $=id=>document.getElementById(id);if(!$('spf-overview'))return;
 const processLabels={delivered:'已交付 · 跳过',success:'成功',warning:'警告 / 异常',rejected:'机器不通过',failed:'失败',error:'失败',pending:'等待处理',not_requested:'未请求',unknown:'结果待核实'};
 let data=null,filtered=[],page=0,loading=false;const pageSize=20;
 function render(){
   if(!data)return;const key=$('spf-roster-group').value,query=$('spf-roster-query').value.trim().toLowerCase();
   filtered=data.rows.filter(row=>row.groups.includes(key)&&(!query||[row.student_id,row.category,row.message,row.review_message,row.version_note].join(' ').toLowerCase().includes(query)));
   const pages=Math.ceil(filtered.length/pageSize);page=Math.max(0,Math.min(page,Math.max(0,pages-1)));
   $('spf-roster-body').replaceChildren();
   filtered.slice(page*pageSize,(page+1)*pageSize).forEach(row=>{
     const tr=document.createElement('tr'),idCell=document.createElement('td'),link=document.createElement('button');
     link.textContent=row.student_id;link.title='查看该学号完整流程';link.onclick=()=>SPF.openStudent(row.student_id).catch(error=>alert(error.message));idCell.append(link);tr.append(idCell);
     [row.category,SPF.labels[row.review_status]||row.review_status,processLabels[row.processing_status]||row.processing_status].forEach(value=>{const td=document.createElement('td');td.textContent=value;tr.append(td);});
     const reason=document.createElement('td');reason.className='spf-reason';reason.textContent=row.message||row.review_message;
     if(row.version_note){const small=document.createElement('small');small.textContent=row.version_note;reason.append(small);}tr.append(reason);
     const batch=document.createElement('td');batch.className='spf-batch';batch.textContent=row.batch_id||'—';const time=document.createElement('small');time.textContent=row.at||'';batch.append(time);tr.append(batch);
     $('spf-roster-body').append(tr);
   });
   if(!filtered.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=6;td.textContent='当前筛选没有学生';tr.append(td);$('spf-roster-body').append(tr);}
   $('spf-roster-count').textContent=`${data.groups[key].label}：共 ${filtered.length} 人${query?'（已应用关键词筛选）':''}。点击学号查看详细流程。`;
   $('spf-roster-page').textContent=`${pages?page+1:0} / ${pages}`;$('spf-roster-prev').disabled=page===0;$('spf-roster-next').disabled=page+1>=pages;
   $('spf-roster-ids').value=filtered.map(row=>row.student_id).join('\n');
   $('spf-roster-txt').disabled=!filtered.length;$('spf-roster-csv').disabled=!filtered.length;
   $('spf-roster-json').disabled=!filtered.length;
   $('spf-stats').querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.group===key)));
 }
 async function refresh(){
   if(loading)return;loading=true;$('spf-overview-refresh').disabled=true;$('spf-overview-note').className='';
   $('spf-overview-note').textContent='正在核对全年级审核、图片版本与历史处理记录…';
   try{
     const updated=await SPF.get('overview');data=updated;const previous=$('spf-roster-group').value||'remaining';
     $('spf-stats').replaceChildren();$('spf-roster-group').replaceChildren();
     Object.entries(data.groups).forEach(([key,group])=>{
       const option=document.createElement('option');option.value=key;option.textContent=group.label+'（'+group.count+'）';$('spf-roster-group').append(option);
       const button=document.createElement('button');button.dataset.group=key;button.textContent=group.label;const count=document.createElement('strong');count.textContent=String(group.count);button.append(count);
       button.onclick=()=>{$('spf-roster-group').value=key;page=0;render();};$('spf-stats').append(button);
     });
     $('spf-roster-group').value=previous in data.groups?previous:'remaining';
     $('spf-overview-time').textContent='统计时间：'+data.generated_at;
     const notes=[data.enabled?'审核归档保护已开启。':'审核归档保护未开启：请在主程序开启，普通已审核照片才能跳过处理。',`独立已交付锁定 ${data.groups.delivered.count} 人，不受审核开关、新原图或改参影响。`];
     if(data.outside_roster.length)notes.push(`另有 ${data.outside_roster.length} 个名单外学号未计入。`);
     if(data.warnings.length)notes.push(`有 ${data.warnings.length} 项统计提示，历史记录可能不完整；下载名单前请核查。`);
     $('spf-overview-note').textContent=notes.join(' ');$('spf-overview-note').className=data.warnings.length?'warning':'';
     $('spf-warning-details').hidden=!data.warnings.length;$('spf-warning-list').replaceChildren();
     data.warnings.forEach(item=>{const li=document.createElement('li');li.textContent=item.join('：');$('spf-warning-list').append(li);});render();
   }catch(error){$('spf-overview-note').className='error';$('spf-overview-note').textContent=(data?'刷新失败，下面保留上次统计，不是实时数据。':'暂时无法统计。')+error.message;}
   finally{loading=false;$('spf-overview-refresh').disabled=false;}
 }
 function save(text,suffix,mime){
   const blob=new Blob(['\ufeff',text],{type:mime+';charset=utf-8'}),url=URL.createObjectURL(blob),link=document.createElement('a');
   const label=data.groups[$('spf-roster-group').value].label;link.href=url;link.download='全年级_'+label+'_'+suffix;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
 }
 function csvCell(value){let text=String(value??'');if(/^\s*[=+@-]/.test(text))text="'"+text;return '"'+text.replaceAll('"','""')+'"';}
 $('spf-roster-txt').onclick=()=>save(filtered.map(row=>row.student_id).join('\r\n')+'\r\n','学号.txt','text/plain');
 $('spf-roster-json').onclick=()=>save(JSON.stringify({schema_version:1,kind:'roster_query',group:$('spf-roster-group').value,generated_at:data.generated_at,student_ids:filtered.map(row=>row.student_id),rows:filtered},null,2),'名单.json','application/json');
 $('spf-roster-csv').onclick=()=>{
   const rows=[['学号','分类','人工审核状态','处理状态','处理说明','审核说明','版本说明','处理批次','记录时间','依据文件'],
     ...filtered.map(row=>[row.student_id,row.category,SPF.labels[row.review_status]||row.review_status,row.processing_status,row.message,row.review_message,row.version_note,row.batch_id,row.at,row.evidence])];
   save(rows.map(row=>row.map(csvCell).join(',')).join('\r\n')+'\r\n','明细.csv','text/csv');
 };
 $('spf-overview-refresh').onclick=refresh;$('spf-roster-group').onchange=()=>{page=0;render();};$('spf-roster-query').oninput=()=>{page=0;render();};
 $('spf-roster-prev').onclick=()=>{page--;render();};$('spf-roster-next').onclick=()=>{page++;render();};window.addEventListener('focus',refresh);refresh();
})();
</script>
"""


def enhance_gallery_html(document: str) -> str:
    if 'id="spf-shared-search"' in document:
        return document
    document = document.replace("</head>", SHARED_STYLE + OVERVIEW_STYLE + "</head>", 1)
    document = document.replace("</header>", "</header>" + search_markup("gallery") + overview_markup(), 1)
    return document.replace("</body>", SHARED_SCRIPT + OVERVIEW_SCRIPT + "</body>", 1)


def review_page() -> str:
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>StudentPhotoFlow · 逐张审核归档</title>""" + SHARED_STYLE + """<style>
body{margin:0;background:#f4f7fb;color:#172033;font-family:'Microsoft YaHei',system-ui,sans-serif}header{padding:15px 24px;background:#17365d;color:white}header h1{margin:0;font-size:22px}
.review-controls{padding:12px 24px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}.review-controls select,.review-controls button{font:inherit;padding:7px 12px;border:1px solid #bbcadd;border-radius:8px;background:white}
main{max-width:1100px;margin:auto;padding:0 20px 24px}.review-card{background:white;border:1px solid #dbe3ed;border-radius:14px;padding:18px;text-align:center}#review-id{font-size:30px;margin:0 0 10px;overflow-wrap:anywhere}
#review-image{max-width:100%;width:auto;height:clamp(160px,calc(100vh - 460px),53vh);object-fit:contain;background:#edf2f7}#review-note{color:#56677f;white-space:pre-wrap}
.decisions{display:flex;justify-content:center;gap:10px;flex-wrap:wrap;margin-top:16px}.decisions button{border:1px solid #c3cede;background:white;padding:12px 20px;font:inherit;border-radius:8px;cursor:pointer}
.decisions .yes{background:#067647;color:white}.decisions .no{background:#b42318;color:white}.decisions button:disabled{opacity:.4;cursor:not-allowed}
#review-error{color:#b42318;min-height:22px}summary{cursor:pointer;padding:12px}.hint{font-size:12px;color:#64748b}
</style></head><body><header><h1>人工审核 · 通过即归档</h1></header>""" + search_markup("review") + """
<div class="review-controls"><label>审核范围 <select id="review-filter"><option value="pending">待审核 / 图片已变化</option><option value="all">全部处理记录</option><option value="approved">已通过归档</option><option value="rejected">不通过</option><option value="skipped">已跳过</option></select></label><button id="reload-queue">刷新队列</button><span id="review-position"></span></div>
<main><div class="review-card"><h2 id="review-id">正在加载…</h2><span id="review-status" class="spf-review-badge"></span><div><img id="review-image" alt="当前学生最终结果" hidden></div><p id="review-note"></p>
<div class="decisions"><button id="review-yes" class="yes">是 · 通过并归档</button><button id="review-previous">返回上一张</button><button id="review-no" class="no">否 · 不通过</button><button id="review-skip">跳过</button><button id="review-undo">撤销最近标记</button></div>
<p id="review-error" role="status"></p><p class="hint">返回上一张只切换图片；撤销会回退最近审核。已归档版本不会再次处理，新原图需重新审核。</p></div>
<details><summary>查看此学号的详细流程与检测结果</summary><div id="review-detail"></div></details></main>""" + SHARED_SCRIPT + r"""
<script>
(() => {
 const $=id=>document.getElementById(id);let ids=[],cursor=0,current=null,back=[],lastAction=null,busy=false,request=0,enabled=false,imageReady=false;
 function controls(){const can=enabled&&current&&current.in_roster&&current.exists&&!current.delivery_locked&&!busy;
   $('review-yes').disabled=!can||!current.can_approve||!imageReady;$('review-no').disabled=!can;$('review-skip').disabled=!can;
   $('review-previous').disabled=busy||!back.length;$('review-undo').disabled=busy||!lastAction||!enabled;
 }
 async function show(sid,remember=true){
   const nonce=++request;busy=true;imageReady=false;controls();$('review-error').textContent='';$('review-image').hidden=true;
   try{const detail=await SPF.get('student',{student_id:sid});if(nonce!==request)return;
     if(remember&&current&&current.student_id!==sid)back.push(current.student_id);current=detail;enabled=detail.review_enabled;
     $('review-id').textContent=sid;$('review-status').textContent=SPF.labels[detail.status]||detail.status;
     const img=$('review-image'),file=(detail.delivery_locked?detail.archive_file:null)||detail.result_file||detail.original_file;img.hidden=!file;
     img.onload=()=>{if(nonce===request){imageReady=true;controls();}};
     img.onerror=()=>{if(nonce===request){imageReady=false;controls();$('review-error').textContent='图片加载失败，请刷新后再审核。';}};
     if(file)img.src=SPF.fileUrl(file)+'?v='+detail.version;
     $('review-note').textContent=(detail.delivery_locked?'已交付锁定，仅查看，不能重复标记':(detail.result_file?'最终结果':'无有效最终结果；仅显示原图，不能通过归档'))+'\n'+detail.message;
     $('review-position').textContent=`队列 ${Math.min(cursor+1,ids.length)}/${ids.length}｜${detail.in_roster?'全年级名单内':'名单外，不可审核'}`;
     SPF.detailView(detail,$('review-detail'));
   }catch(error){$('review-error').textContent=error.message;current=null;$('review-id').textContent=sid;$('review-image').hidden=true;$('review-detail').replaceChildren();}
   finally{if(nonce===request){busy=false;controls();}}
 }
 window.loadReviewStudent=async sid=>{if(busy)return;const index=ids.indexOf(sid);if(index>=0)cursor=index;await show(sid);};
 async function refresh(){const data=await SPF.refreshGate();if(data){enabled=data.enabled;lastAction=data.last_action_id;}controls();return data;}
 async function loadQueue(){if(busy)return;busy=true;controls();
   try{const data=await SPF.get('queue',{filter:$('review-filter').value});enabled=data.enabled;ids=data.student_ids;lastAction=data.last_action_id;cursor=0;back=[];current=null;$('review-status').textContent='';$('review-detail').replaceChildren();$('review-position').textContent=`队列 0/${ids.length}`;
     if(!enabled){$('review-id').textContent='请先启用审核归档';$('review-note').textContent='在主程序校验保存全年级学号名单后开启此功能。';$('review-image').hidden=true;}
     else if(!ids.length){$('review-id').textContent='当前范围没有待审核结果';$('review-image').hidden=true;$('review-note').textContent='可切换审核范围，或使用顶部学号搜索查看详情。';}
   }catch(error){$('review-error').textContent=error.message;ids=[];}
   finally{busy=false;controls();}if(enabled&&ids.length)await show(ids[0],false);
 }
 async function mark(decision){if(busy||!current||(decision==='approved'&&!imageReady))return;busy=true;controls();$('review-error').textContent='正在保存…';
   try{const data=await SPF.post({action:'mark',student_id:current.student_id,decision,expected_version:current.version,expected_revision:current.revision});lastAction=data.action_id;
     if(ids[cursor]===current.student_id)cursor++;await refresh();busy=false;
     if(cursor<ids.length)await show(ids[cursor]);else{back.push(current.student_id);current=null;$('review-id').textContent='本轮审核已结束';$('review-status').textContent='标记已保存';$('review-image').hidden=true;$('review-note').textContent='可刷新队列，审核跳过项或切换到已归档结果。';$('review-error').textContent='';$('review-detail').replaceChildren();}
   }catch(error){$('review-error').textContent=error.message;}finally{busy=false;controls();}
 }
 $('review-yes').onclick=()=>mark('approved');$('review-no').onclick=()=>mark('rejected');$('review-skip').onclick=()=>mark('skipped');
 $('review-previous').onclick=async()=>{if(busy||!back.length)return;const sid=back.pop(),index=ids.indexOf(sid);if(index>=0)cursor=index;await show(sid,false);};
 $('review-undo').onclick=async()=>{if(busy||!lastAction)return;busy=true;controls();try{const data=await SPF.post({action:'undo',action_id:lastAction});await refresh();busy=false;const index=ids.indexOf(data.student_id);if(index>=0)cursor=index;await show(data.student_id);}catch(error){$('review-error').textContent=error.message;}finally{busy=false;controls();}};
 $('reload-queue').onclick=loadQueue;$('review-filter').onchange=loadQueue;controls();loadQueue();
})();
</script></body></html>"""
