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
 const labels={approved:'已通过 · 已归档',rejected:'审核不通过',skipped:'已跳过',pending:'待审核',stale:'图片已变化 · 需重新审核'};
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
     document.getElementById('spf-gate').textContent=data.enabled?`全年级 ${c.total} 人｜有处理记录 ${c.processed}｜归档 ${c.approved}｜待处理 ${c.missing}`:'审核未启用：请在主程序校验保存全年级名单后开启';
     const link=document.getElementById('spf-review-link');link.href=data.enabled?'/reports/'+token+'/review.html':'#';link.dataset.enabled=String(data.enabled);
     document.querySelectorAll('article.card').forEach(card=>{
       let badge=card.querySelector('.spf-review-badge');if(!badge){badge=document.createElement('span');badge.className='spf-review-badge';(card.querySelector('.meta')||card).append(badge);}
       const status=(data.statuses||{})[card.dataset.studentId];badge.textContent=labels[status]||'未列入审核名单';
     });
     return data;
   } catch(error){document.getElementById('spf-gate').textContent=error.message;return null;}
 }
 window.SPF={get,post,fileUrl,detailView,refreshGate,labels};
 const form=document.getElementById('spf-search-form');
 form.addEventListener('submit',async event=>{
   event.preventDefault();const sid=document.getElementById('spf-student-id').value.trim();if(!sid)return;
   try{if(document.getElementById('spf-shared-search').dataset.mode==='review'){await window.loadReviewStudent(sid);}
   else{const detail=await get('student',{student_id:sid});detailView(detail,document.getElementById('spf-detail-body'));document.getElementById('spf-detail').showModal();}}
   catch(error){alert(error.message);}
 });
 document.getElementById('spf-close-detail').onclick=()=>document.getElementById('spf-detail').close();
 document.getElementById('spf-review-link').onclick=event=>{if(event.currentTarget.dataset.enabled!=='true'){event.preventDefault();alert('请先在主程序校验保存全年级名单，并启用审核归档。');}};
 window.addEventListener('focus',refreshGate);refreshGate();
})();
</script>
"""


def enhance_gallery_html(document: str) -> str:
    if 'id="spf-shared-search"' in document:
        return document
    document = document.replace("</head>", SHARED_STYLE + "</head>", 1)
    document = document.replace("</header>", "</header>" + search_markup("gallery"), 1)
    return document.replace("</body>", SHARED_SCRIPT + "</body>", 1)


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
 function controls(){const can=enabled&&current&&current.in_roster&&current.exists&&!busy;
   $('review-yes').disabled=!can||!current.can_approve||!imageReady;$('review-no').disabled=!can;$('review-skip').disabled=!can;
   $('review-previous').disabled=busy||!back.length;$('review-undo').disabled=busy||!lastAction||!enabled;
 }
 async function show(sid,remember=true){
   const nonce=++request;busy=true;imageReady=false;controls();$('review-error').textContent='';$('review-image').hidden=true;
   try{const detail=await SPF.get('student',{student_id:sid});if(nonce!==request)return;
     if(remember&&current&&current.student_id!==sid)back.push(current.student_id);current=detail;enabled=detail.review_enabled;
     $('review-id').textContent=sid;$('review-status').textContent=SPF.labels[detail.status]||detail.status;
     const img=$('review-image'),file=detail.result_file||detail.original_file;img.hidden=!file;
     img.onload=()=>{if(nonce===request){imageReady=true;controls();}};
     img.onerror=()=>{if(nonce===request){imageReady=false;controls();$('review-error').textContent='图片加载失败，请刷新后再审核。';}};
     if(file)img.src=SPF.fileUrl(file)+'?v='+detail.version;
     $('review-note').textContent=(detail.result_file?'最终结果':'无有效最终结果；仅显示原图，不能通过归档')+'\n'+detail.message;
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
