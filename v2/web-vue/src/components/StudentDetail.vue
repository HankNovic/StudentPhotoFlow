<script setup>
import {ref,computed,watch,onMounted,onUnmounted} from 'vue';
import {ElMessage,ElMessageBox} from 'element-plus';
import {api,labels,report,url} from '../api';
import {state,refresh} from '../workspace';
import Photo from './Photo.vue';
import Stages from './Stages.vue';
import ConfigFields from './ConfigFields.vue';
import ActionHelp from './ActionHelp.vue';
import {useDraft,confirmLeave} from '../drafts';
import {newRequestId,reviewState,taskLabels} from '../review';
const props=defineProps({id:String,visibleIds:{type:Array,default:()=>[]}}),emit=defineEmits(['update:id']);
const detail=ref(null),preview=ref(null),busy=ref(''),settings=ref([]),historyOpen=ref([]),config=ref({}),jobs=ref([]),batches=ref([]),cohort=ref(''),cohortName=ref(''),plan=ref(null);
const initialConfig=ref(''),navigation=ref([]),loadError=ref(''),notice=ref(null);
let timer,epoch=0,loadSequence=0,planSequence=0;
const archived=computed(()=>state.allCohorts.find(c=>c.id===cohort.value)?.archived);
const stage=computed(()=>reviewState(detail.value,jobs.value,batches.value,archived.value));
const source=computed(()=>stage.value.source),result=computed(()=>stage.value.result);
const disabled=computed(()=>!!busy.value||!!loadError.value);
const position=computed(()=>navigation.value.indexOf(props.id));
const readyPlan=computed(()=>!!plan.value?.requestId&&plan.value.items.some(i=>i.execute));
const progressText=computed(()=>({load:'正在加载详情',reload:'正在刷新详情',upload:'正在保存原始照片',replace:'正在提交替换操作',plan:'正在检查处理计划',start:'正在创建正式处理任务',preview:'正在生成临时预览',deliver:'正在生成交付包',confirm:'正在确认实际交付'})[busy.value]||(busy.value.startsWith('review-')?'正在提交审核操作':'正在下载交付包'));
const planReason=computed(()=>plan.value?plan.value.items.map(i=>i.execute?'检查通过，可以开始生成正式成片。':i.reason).join('；'):'请先检查处理计划；这一步不会生成正式成片。');
const stageText=computed(()=>stage.value.lockReason||(
  stage.value.prepared?'已有待交付包，请下载并在实际交付后确认；不要重复生成。':
  stage.value.delivered?'照片已实际交付。需要重新制作时，请先启动替换流程。':
  detail.value?.replacement&&!source.value?'替换流程已启动，请上传替换照片。':
  !source.value?'尚无原始照片，请先上传；上传后不会自动开始处理。':
  detail.value?.status==='approved'?'当前正式成片已审核通过，下一步生成交付包。':
  detail.value?.status==='review'?'请审核上方当前正式成片；试处理预览不参与审核。':
  detail.value?.status==='review_rejected'?'当前正式成片已退回。可上传其他原图或调整参数后检查计划；退回不会自动重新处理。':
  '请检查处理计划，再生成正式成片；完成后仍需人工审核。'));
useDraft('single',computed(()=>!!props.id&&!!initialConfig.value&&JSON.stringify(config.value)!==initialConfig.value),()=>{config.value=JSON.parse(initialConfig.value);});
function invalidatePlan(){planSequence++;plan.value=null;}
const context=()=>({sid:props.id,cid:cohort.value,epoch});
const current=c=>c.epoch===epoch&&c.sid===props.id&&c.cid===cohort.value;
const call=(c,p,b,m)=>api(p,b,m,c.cid);
async function close(){if(busy.value){ElMessage.info('正在提交操作，请等待返回后关闭；已创建的后台任务不受关闭影响。');return;}if(await confirmLeave())emit('update:id','');}
async function load(){
  const c=context(),seq=++loadSequence;if(!c.sid||!c.cid)return;
  try{
    const [d,j,b]=await Promise.all([call(c,'students/'+encodeURIComponent(c.sid)),call(c,'jobs'),call(c,'deliveries')]);
    if(!current(c)||seq!==loadSequence)return;
    const newSource=d.sources.at(-1),newResult=d.results.filter(r=>r.source_id===newSource?.id).at(-1);
    if(detail.value&&(source.value?.id!==newSource?.id||result.value?.id!==newResult?.id||detail.value.revision!==d.revision)){
      invalidatePlan();preview.value=null;
    }
    detail.value=d;jobs.value=j.filter(j=>j.plan.items.some(i=>i.student_id===c.sid));batches.value=b.filter(b=>b.items.some(i=>i.student_id===c.sid));loadError.value='';
  }catch(e){if(current(c)&&seq===loadSequence){loadError.value='页面刷新失败：'+(e.message||String(e));invalidatePlan();}throw e;}
}
async function reloadAfter(c,message){
  if(!current(c))return;
  notice.value={type:'success',text:message};ElMessage.success(message);
  try{await load();await refresh();}
  catch(e){if(current(c)){notice.value={type:'warning',text:message+' 操作已提交，但页面刷新失败：'+(e.message||String(e))+'。请刷新确认结果，不要重复提交。'};ElMessage.warning(notice.value.text);}}
}
// Handles promises from both Vue click events and Element Plus upload callbacks.
async function perform(key,fn){
  if(busy.value)return;
  const c=context();busy.value=key;notice.value=null;
  try{await fn(c);}catch(e){if(current(c)&&e!=='cancel'&&e!=='close'){notice.value={type:'error',text:e.message||String(e)};report(e);}}
  finally{if(current(c))busy.value='';}
}
watch(()=>props.id,async(id,old)=>{
  epoch++;loadSequence++;invalidatePlan();busy.value='';detail.value=null;preview.value=null;jobs.value=[];batches.value=[];loadError.value='';notice.value=null;initialConfig.value='';historyOpen.value=[];settings.value=[];
  if(!id)return;
  if(!old||cohort.value!==state.cohort){navigation.value=[...props.visibleIds];if(!navigation.value.includes(id))navigation.value.push(id);}
  cohort.value=state.cohort;cohortName.value=state.cohortName;
  await perform('load',async c=>{try{const saved=await api('config');if(!current(c))return;config.value={...saved.defaults,...saved.saved};initialConfig.value=JSON.stringify(config.value);await load();}catch(e){if(current(c))loadError.value='详情加载失败：'+(e.message||String(e));throw e;}});
},{immediate:true});
watch(()=>JSON.stringify(config.value),()=>{invalidatePlan();preview.value=null;},{flush:'sync'});
async function move(delta){
  if(busy.value||cohort.value!==state.cohort||!await confirmLeave())return;
  // Keep the opening order when an audit changes the live status filter.
  const next=navigation.value[position.value+delta];if(next)emit('update:id',next);
}
function upload(file){return perform('upload',async c=>{
  if(!stage.value.upload)throw Error(stage.value.lockReason||'当前阶段不允许上传照片，请先处理审核或交付状态。');
  const body=new FormData();body.append('photo',file.raw);
  const d=await call(c,'students/'+encodeURIComponent(c.sid)+'/photos',body);
  if(current(c)){invalidatePlan();preview.value=null;}
  await reloadAfter(c,d.changed?'原始照片已保存，请检查处理计划；尚未开始处理。':'照片内容未变化，未新增版本');
});}
function replace(){return perform('replace',async c=>{
  if(!stage.value.replace)throw Error('仅已实际交付的照片可以启动替换流程。');
  const {value}=await ElMessageBox.prompt('保留原交付历史；启动后还需上传新照片并重新处理。请填写原因。','启动照片替换',{inputValidator:v=>!!v?.trim()||'请填写原因',confirmButtonText:'启动替换'});
  if(!current(c))return;
  await call(c,'students/'+encodeURIComponent(c.sid)+'/replacement',{reason:value});invalidatePlan();preview.value=null;
  await reloadAfter(c,'替换流程已启动，请上传新照片');
});}
function replan(){return perform('plan',async c=>{
  invalidatePlan();const seq=planSequence,values=JSON.stringify(config.value);
  if(!stage.value.process)throw Error(stage.value.lockReason||'当前阶段不可处理。');
  const requestId=newRequestId();
  const data=await call(c,'processing-jobs',{student_ids:[c.sid],config:JSON.parse(values),dry_run:true});
  if(!current(c)||seq!==planSequence||values!==JSON.stringify(config.value))return;
  plan.value={...data,requestId};notice.value={type:data.items.some(i=>i.execute)?'success':'warning',text:planReason.value};
});}
function start(){return perform('start',async c=>{
  if(!stage.value.process||!readyPlan.value)throw Error('请先重新检查处理计划并取得有效请求编号。');
  const selectedPlan=plan.value;
  try{
    const j=await call(c,'processing-jobs',{student_ids:[c.sid],config:selectedPlan.config,dry_run:false,expected_revision:selectedPlan.revision,request_id:selectedPlan.requestId});
    if(current(c)){invalidatePlan();preview.value=null;initialConfig.value=JSON.stringify(config.value);}
    await reloadAfter(c,j.status==='skipped'?'已有结果或当前规则不允许重复处理，请重新检查计划。':'正式处理任务已创建，关闭窗口后仍继续执行；完成后需要人工审核。');
  }catch(e){if(current(c))invalidatePlan();throw e;}
});}
function test(){return perform('preview',async c=>{
  preview.value=null;
  if(!stage.value.process)throw Error(stage.value.lockReason||'当前阶段不可试处理。');
  const values=JSON.stringify(config.value),d=await call(c,'students/'+encodeURIComponent(c.sid)+'/preview',JSON.parse(values));
  if(!current(c)||values!==JSON.stringify(config.value))return;
  preview.value=d;invalidatePlan();
  notice.value={type:['success','warning'].includes(d.status)?'success':'warning',text:d.artifact?'试处理预览已完成；不会成为正式成片，不能直接审核。':'试处理未生成可用照片，请查看下方阶段原因。'};
});}
function review(decision){return perform('review-'+decision,async c=>{
  if(!(decision==='approved'?stage.value.approve:decision==='rejected'?stage.value.reject:stage.value.undo))throw Error('当前阶段没有可执行的审核操作，请刷新详情。');
  const resultId=result.value.id,revision=detail.value.revision;let reason='';
  if(decision==='rejected')reason=(await ElMessageBox.prompt('退回不会自动重新处理。原因可留空。','退回当前正式成片',{confirmButtonText:'保存退回'})).value||'';
  if(!current(c))return;
  await call(c,'reviews',{student_id:c.sid,result_id:resultId,decision,expected_revision:revision,reason});
  invalidatePlan();preview.value=null;
  await reloadAfter(c,decision==='approved'?'当前正式成片已审核通过，请生成交付包。':decision==='rejected'?'当前正式成片已退回；未自动重新处理。':'审核决定已撤回，请重新审核当前正式成片。');
});}
function deliver(){return perform('deliver',async c=>{
  if(!stage.value.deliver)throw Error('请确认正式成片已审核通过，且没有待交付包。');
  await call(c,'deliveries',{student_ids:[c.sid]});
  await reloadAfter(c,'已生成单人交付包，下载后仍需确认已交付');
});}
function confirm(b){return perform('confirm',async c=>{
  await ElMessageBox.confirm('确认这个单人交付包已实际交给学生？下载不等于交付。','确认已交付');
  if(!current(c))return;
  await call(c,'deliveries/'+b.id+'/confirm',{});await reloadAfter(c,'已记录实际交付；原版本和交付历史保留。');
});}
function downloadBatch(b){return perform('download-'+b.id,async c=>{
  const response=await fetch(url('deliveries/'+b.id+'/download',c.cid));
  if(!response.ok){const text=await response.text();let message=text;try{const d=JSON.parse(text);message=d.message||JSON.stringify(d.detail||d);}catch{}throw Error(message||'交付包下载失败');}
  const href=URL.createObjectURL(await response.blob()),a=document.createElement('a');
  a.href=href;a.download='学生照片_'+b.id.slice(0,8)+'.zip';a.click();setTimeout(()=>URL.revokeObjectURL(href),1000);
  if(current(c)){notice.value={type:'success',text:'交付包已下载；实际交给学生后还需确认已交付。'};ElMessage.success(notice.value.text);}
});}
async function retryLoad(){return perform('reload',async c=>{if(!initialConfig.value){const saved=await api('config');if(!current(c))return;config.value={...saved.defaults,...saved.saved};initialConfig.value=JSON.stringify(config.value);}await load();});}
onMounted(()=>{timer=setInterval(()=>{if(props.id&&!busy.value)load().catch(()=>{});},2000);});onUnmounted(()=>{clearInterval(timer);epoch++;});
</script>
<template>
 <el-dialog :model-value="!!id" @update:model-value="close" :title="'学生 '+id+' · 流程与审核'" width="min(1120px,96vw)" destroy-on-close>
  <el-alert v-if="loadError" :title="loadError" type="error" :closable="false"/>
  <div v-if="loadError" class="toolbar"><el-button :loading="busy==='reload'" :disabled="!!busy" @click="retryLoad">刷新详情</el-button><ActionHelp label="刷新详情" text="重新读取当前学生记录；不会重新提交处理或审核。"/></div>
  <el-skeleton v-if="!detail&&busy" :rows="4" animated/>
  <template v-if="detail">
   <p>固定届次：{{cohortName}} · 学号：{{id}} <span v-if="detail.name">· 姓名：{{detail.name}}</span></p>
   <el-alert v-if="cohort!==state.cohort" title="侧栏已切换；本窗口操作仍属于打开时的届次。关闭后可查看新届次。" type="info" :closable="false"/>
   <div class="toolbar"><el-tag>{{labels[detail.status]||detail.status}}</el-tag><el-tag v-if="detail.replacement">替换流程中</el-tag><span class="muted">修订号 {{detail.revision}}</span></div>
   <el-alert :title="stageText" :type="stage.lockReason?'warning':'info'" :closable="false" data-testid="review-stage"/>
   <p v-if="busy" class="muted" role="status">{{progressText}}，请稍候；当前操作返回前不能重复提交。</p>
   <el-alert v-if="notice" :title="notice.text" :type="notice.type" :closable="false" class="spaced" data-testid="review-notice"/>
   <div class="comparison"><Photo :artifact="source" label="原始照片"/><Photo :artifact="result?.artifact" label="当前正式成片"/></div>
   <section v-if="stage.upload||stage.replace||stage.process" class="review-section" aria-label="照片制作">
    <h3>照片制作</h3>
    <div class="toolbar">
     <span v-if="stage.upload" class="action-pair"><el-upload :auto-upload="false" :show-file-list="false" accept="image/*" :on-change="upload" :disabled="disabled"><el-button :loading="busy==='upload'" :disabled="disabled">{{detail.replacement?'上传替换照片':source?'更换原始照片':'上传照片'}}</el-button></el-upload><ActionHelp label="上传照片" text="保存原始照片，不会自动开始处理。相同内容不会新增版本。"/></span>
     <span v-if="stage.replace" class="action-pair"><el-button @click="replace" :loading="busy==='replace'" :disabled="disabled">启动替换流程</el-button><ActionHelp label="启动替换流程" text="仅用于已交付照片重新制作；保留历史交付，启动后还要上传照片并重新处理。"/></span>
    </div>
    <template v-if="stage.process">
     <p class="muted">引擎 {{config.background_mode}} · 背景 {{config.background_color}} · 输出 {{config.background_mode==='hivision'?config.hivision_width:config.crop_enabled?config.crop_width:source?.width}} × {{config.background_mode==='hivision'?config.hivision_height:config.crop_enabled?config.crop_height:source?.height}}。参数修改后请重新检查计划。</p>
     <el-collapse v-model="settings"><el-collapse-item name="settings"><template #title><span>仅本次调整处理参数</span><ActionHelp label="仅本次调整处理参数" text="只对当前学生本次处理生效，不修改全局配置；修改后需要重新检查计划，不会立即处理。"/></template><fieldset :disabled="disabled" class="config-fieldset"><ConfigFields v-model="config" local-settings/></fieldset></el-collapse-item></el-collapse>
     <div class="toolbar">
      <span class="action-pair"><el-button @click="replan" :loading="busy==='plan'" :disabled="disabled">检查处理计划</el-button><ActionHelp label="检查处理计划" text="只检查原图、参数及处理规则；不会创建任务、调用处理服务或生成正式成片。"/></span>
      <span class="action-pair"><el-button type="primary" @click="start" :loading="busy==='start'" :disabled="disabled||!readyPlan">开始生成正式成片</el-button><ActionHelp label="开始生成正式成片" :text="'按检查后的配置生成正式结果，完成后仍需人工审核。'+(readyPlan?'关闭窗口不会停止已创建的任务。':planReason)"/></span>
      <span class="action-pair"><el-button @click="test" :loading="busy==='preview'" :disabled="disabled">试处理预览</el-button><ActionHelp label="试处理预览" text="实际调用处理流程，但只生成临时预览，不会成为正式成片，不能直接审核。"/></span>
     </div><p class="muted" data-testid="single-plan">{{planReason}}</p>
    </template>
   </section>
   <section v-if="stage.approve||stage.reject||stage.undo" class="review-section" aria-label="正式成片审核">
    <h3>正式成片审核</h3><p class="muted">审核对象仅为上方“当前正式成片”，不包括试处理预览。</p>
    <div class="toolbar">
     <span v-if="stage.approve" class="action-pair"><el-button type="success" :loading="busy==='review-approved'" :disabled="disabled" @click="review('approved')">审核通过当前正式成片</el-button><ActionHelp label="审核通过" text="确认当前正式成片合格，保存后进入待交付，可以生成交付包。"/></span>
     <span v-if="stage.reject" class="action-pair"><el-button type="danger" :loading="busy==='review-rejected'" :disabled="disabled" @click="review('rejected')">退回当前正式成片</el-button><ActionHelp label="退回" text="标记当前正式成片不合格，不会自动重新处理或生成新照片。"/></span>
     <span v-if="stage.undo" class="action-pair"><el-button :loading="busy==='review-pending'" :disabled="disabled" @click="review('pending')">撤回当前审核</el-button><ActionHelp label="撤回审核" text="将已作出的审核决定恢复为待审核；不会生成或删除照片。"/></span>
    </div>
   </section>
   <section v-if="stage.deliver||batches.length" class="review-section" aria-label="照片交付">
    <h3>照片交付</h3>
    <div v-if="stage.deliver" class="toolbar"><el-button :loading="busy==='deliver'" :disabled="disabled" @click="deliver">生成单人交付包</el-button><ActionHelp label="生成交付包" text="打包已审核通过的正式成片；生成、下载均不等于交付，实际交给学生后还需确认。"/></div>
    <el-card v-for="b in batches" :key="b.id" shadow="never"><p>交付记录 {{b.id.slice(0,8)}} · {{({prepared:'待确认实际交付',delivered:'已实际交付',cancelled:'已取消'})[b.status]||b.status}} · {{b.items.length}} 人</p>
     <div class="toolbar"><span v-if="!b.historical&&b.status!=='cancelled'" class="action-pair"><el-button :loading="busy==='download-'+b.id" :disabled="!!busy" @click="downloadBatch(b)">下载交付包</el-button><ActionHelp label="下载交付包" text="下载已有交付文件，不会自动把学生标记为已交付；多人批次下载的是整个原交付包。"/></span>
     <span v-if="b.status==='prepared'&&b.items.length===1&&!stage.lockReason" class="action-pair"><el-button :loading="busy==='confirm'" :disabled="disabled" @click="confirm(b)">确认已交付</el-button><ActionHelp label="确认已交付" text="确认照片已实际交给学生，保存交付事实。尚未发送时请不要确认。"/></span></div>
     <p v-if="b.status==='prepared'" class="muted">{{b.items.length>1?'多人交付包请在照片交付页面统一确认。':'下载后请在实际交付时确认。'}}取消待交付包请前往“照片交付”。</p>
    </el-card>
   </section>
   <h3 v-if="result">正式处理阶段结果</h3><Stages v-if="result" :result="result"/>
   <section v-if="preview||busy==='preview'" class="review-section" aria-label="临时试处理预览">
    <h3>试处理阶段结果（非正式成片）</h3><p class="muted">此处照片不能直接审核；需要检查计划并生成正式成片。</p><p v-if="busy==='preview'" role="status">正在试处理，请等待服务返回；不会自动通过审核。</p><Photo v-if="preview?.artifact" :artifact="preview.artifact" label="临时预览，不参与审核"/><Stages v-if="preview" :result="preview"/>
   </section>
   <el-collapse v-model="historyOpen" data-testid="related-jobs"><el-collapse-item name="jobs" :title="'关联任务 / 历史任务（'+jobs.length+'）'">
    <p class="muted">以下为最近三条关联任务的整批进度，不是当前学生个人进度。更多记录及暂停、继续、结束操作请到“任务进度”。折叠不影响后台任务或轮询。</p>
    <el-empty v-if="!jobs.length" description="暂无关联任务"/>
    <div v-for="j in jobs.slice(0,3)" :key="j.id" class="task-summary"><p>{{j.kind==='import'?'原图接收':j.kind==='delivery'?'交付打包':'照片处理'}} · {{j.id.slice(0,8)}} · {{taskLabels[j.status]||j.status}} · 整批已结束项目 {{j.completed.length}} / {{j.plan.items.length}}</p><p v-if="j.message">{{j.message}}</p><pre v-if="Object.keys(j.errors||{}).length">整批失败记录：{{j.errors}}</pre><p v-if="j.uncertain?.[id]">当前学生需核对：{{j.uncertain[id]}}</p></div>
   </el-collapse-item></el-collapse>
   <el-collapse><el-collapse-item title="版本与操作记录"><pre data-testid="version-history">{{JSON.stringify({history:detail.history,versions:detail.results,delivered:detail.delivered},null,2)}}</pre></el-collapse-item></el-collapse>
   <nav class="toolbar review-navigation" aria-label="学生导航"><el-button @click="move(-1)" :disabled="!!busy||position<=0||cohort!==state.cohort">上一张</el-button><el-button @click="move(1)" :disabled="!!busy||position<0||position>=navigation.length-1||cohort!==state.cohort">下一张</el-button><ActionHelp label="学生导航" text="按打开时的筛选顺序切换学生，不会保存或跳过审核；已有审核决定即时保存。"/><span class="muted">仅切换学生，不写入跳过状态。{{busy?'正在提交操作，请稍候。':''}}</span></nav>
  </template>
 </el-dialog>
</template>
<style scoped>
.review-section{border-top:1px solid #e9e9e7;margin-top:16px;padding-top:14px}.action-pair{display:inline-flex;align-items:center;gap:6px}.config-fieldset{border:0;margin:0;padding:0;min-width:0}.config-fieldset:disabled{pointer-events:none;opacity:.65}.review-navigation{border-top:1px solid #e9e9e7;padding-top:12px}.task-summary{border-bottom:1px solid #e9e9e7;padding:6px 0}.comparison{margin:14px 0}
</style>
