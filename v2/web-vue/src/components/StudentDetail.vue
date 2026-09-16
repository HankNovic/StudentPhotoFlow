<script setup>
import {ref,computed,watch,onMounted,onUnmounted} from 'vue';
import {ElMessage,ElMessageBox} from 'element-plus';
import {api,labels,run,url} from '../api';
import {state,refresh} from '../workspace';
import Photo from './Photo.vue';
import Stages from './Stages.vue';
import ConfigFields from './ConfigFields.vue';
import {useDraft,confirmLeave} from '../drafts';
const props=defineProps({id:String,visibleIds:{type:Array,default:()=>[]}}),emit=defineEmits(['update:id']);
const detail=ref(null),preview=ref(null),busy=ref(false),settings=ref([]),config=ref({}),jobs=ref([]),batches=ref([]),cohort=ref(''),cohortName=ref(''),newVersion=ref(false),plan=ref(null);
let timer,requestId='';
const initialConfig=ref('');
useDraft('single',computed(()=>!!props.id&&!!initialConfig.value&&JSON.stringify(config.value)!==initialConfig.value),()=>{config.value=JSON.parse(initialConfig.value);});
async function close(){if(await confirmLeave())emit('update:id','');}
const source=computed(()=>detail.value?.sources.at(-1));
const result=computed(()=>detail.value?.results.filter(r=>r.source_id===source.value?.id).at(-1));
const active=computed(()=>jobs.value.some(j=>['running','pausing','paused','cancelling','queued'].includes(j.status)));
const archived=computed(()=>state.allCohorts.find(c=>c.id===cohort.value)?.archived);
const locked=computed(()=>active.value||busy.value||archived.value);
const canReview=computed(()=>!!result.value?.artifact&&!detail.value?.status.startsWith('delivered')&&!locked.value);
const position=computed(()=>props.visibleIds.indexOf(props.id));
const call=(p,b,m)=>api(p,b,m,cohort.value);
async function load(){const sid=props.id,cid=cohort.value;if(!sid||!cid)return;const [d,j,b]=await Promise.all([api('students/'+encodeURIComponent(sid),undefined,undefined,cid),api('jobs',undefined,undefined,cid),api('deliveries',undefined,undefined,cid)]);if(sid!==props.id||cid!==cohort.value)return;detail.value=d;jobs.value=j.filter(j=>j.plan.items.some(i=>i.student_id===sid));batches.value=b.filter(b=>b.items.some(i=>i.student_id===sid));}
watch(()=>props.id,async(id)=>{if(!id)return;detail.value=null;cohort.value=state.cohort;cohortName.value=state.cohortName;plan.value=null;preview.value=null;newVersion.value=false;requestId='';await run(async()=>{const saved=await api('config');if(id!==props.id)return;config.value={...saved.defaults,...saved.saved};initialConfig.value=JSON.stringify(config.value);await load();});},{immediate:true});
watch(()=>[JSON.stringify(config.value),newVersion.value],()=>{plan.value=null;requestId='';});
async function review(decision){let reason='';if(decision==='rejected')reason=(await ElMessageBox.prompt('退回原因（可留空）','退回当前成片',{confirmButtonText:'保存'})).value||'';await call('reviews',{student_id:props.id,result_id:result.value.id,decision,expected_revision:detail.value.revision,reason});await load();await refresh();ElMessage.success('审核已保存');}
async function move(delta){if(!await confirmLeave())return;const next=props.visibleIds[position.value+delta];if(next&&cohort.value===state.cohort)emit('update:id',next);}
async function test(){busy.value=true;try{preview.value=await call('students/'+encodeURIComponent(props.id)+'/preview',config.value);}finally{busy.value=false;}}
async function replace(){const {value}=await ElMessageBox.prompt('保留原交付历史，请填写替换原因','启动照片替换',{inputValidator:v=>!!v?.trim()||'请填写原因',confirmButtonText:'启动替换'});await call('students/'+encodeURIComponent(props.id)+'/replacement',{reason:value});await load();await refresh();}
async function upload(file){busy.value=true;try{const body=new FormData();body.append('photo',file.raw);await call('students/'+encodeURIComponent(props.id)+'/photos',body);plan.value=null;requestId='';await load();await refresh();ElMessage.success('原图已保存，请预览单人处理计划');}finally{busy.value=false;}}
async function replan(){plan.value=await call('processing-jobs',{student_ids:[props.id],config:config.value,new_version:newVersion.value,dry_run:true});requestId=crypto.randomUUID();}
async function start(){busy.value=true;try{const j=await call('processing-jobs',{student_ids:[props.id],config:plan.value.config,new_version:newVersion.value,dry_run:false,expected_revision:plan.value.revision,request_id:requestId});plan.value=null;initialConfig.value=JSON.stringify(config.value);await load();await refresh();ElMessage.success(j.status==='skipped'?'已有结果，无需重复提交':'正式单人任务已创建，关闭窗口后继续执行');}finally{busy.value=false;}}
async function deliver(){busy.value=true;try{await call('deliveries',{student_ids:[props.id]});await load();await refresh();ElMessage.success('已生成单人交付包，下载后仍需确认已交付');}finally{busy.value=false;}}
async function confirm(b){await ElMessageBox.confirm('确认这个单人交付包已实际发送？','确认已交付');await call('deliveries/'+b.id+'/confirm',{});await load();await refresh();}
onMounted(()=>{timer=setInterval(()=>{if(props.id)run(load);},2000);});onUnmounted(()=>clearInterval(timer));
</script>
<template>
 <el-dialog :model-value="!!id" @update:model-value="close" :title="'学生 '+id+' · 流程与审核'" width="min(1120px,96vw)" :close-on-click-modal="true" destroy-on-close>
  <template v-if="detail"><p>固定届次：{{cohortName}} · 学号：{{id}} <span v-if="detail.name">· 姓名：{{detail.name}}</span></p><el-alert v-if="cohort!==state.cohort" title="侧栏已切换；本窗口操作仍属于打开时的届次。" type="info" :closable="false"/>
   <div class="toolbar"><el-tag>{{labels[detail.status]||detail.status}}</el-tag><span class="muted">修订号 {{detail.revision}}</span><el-tag v-if="archived">归档只读</el-tag></div>
   <div class="comparison"><Photo :artifact="source" label="原始照片"/><Photo :artifact="result?.artifact" label="当前成片"/></div>
   <div class="toolbar"><el-upload :auto-upload="false" :show-file-list="false" accept="image/*" :on-change="upload" :disabled="locked||(!!detail.delivered&&!detail.replacement)"><el-button :disabled="locked||(!!detail.delivered&&!detail.replacement)">{{source?'上传新版本照片':'补交照片'}}</el-button></el-upload><el-button @click="replace" :disabled="locked">启动替换流程</el-button></div>
   <p class="muted">引擎 {{config.background_mode}} · 背景 {{config.background_color}} · 输出 {{config.background_mode==='hivision'?config.hivision_width:config.crop_enabled?config.crop_width:source?.width}} × {{config.background_mode==='hivision'?config.hivision_height:config.crop_enabled?config.crop_height:source?.height}}。本窗口参数仅本次生效。</p>
   <el-collapse v-model="settings"><el-collapse-item name="settings" title="仅本次调整处理参数"><ConfigFields v-model="config"/></el-collapse-item></el-collapse>
   <div class="toolbar"><el-checkbox v-model="newVersion" :disabled="locked">明确生成新处理版本</el-checkbox><el-button @click="replan" :disabled="locked||!source">预览单人处理计划</el-button><el-button type="primary" @click="start" :loading="busy" :disabled="locked||!plan||!plan.items.some(i=>i.execute)">开始单人处理</el-button><el-button @click="test" :disabled="locked||!source">用当前配置试处理</el-button></div><p v-if="plan">{{plan.items.map(i=>i.execute?'将处理该学生':i.reason).join('；')}}</p>
   <div v-for="j in jobs.slice(0,3)" :key="j.id" class="muted">任务 {{j.id.slice(0,8)}} · {{j.status}} · {{j.completed.length}}/{{j.plan.items.length}}<p v-if="Object.keys(j.errors||{}).length">{{j.errors}}</p></div>
   <div class="toolbar"><el-button @click="move(-1)" :disabled="position<=0||cohort!==state.cohort">上一张</el-button><el-button type="success" :disabled="!canReview" @click="review('approved')">审核通过当前成片</el-button><el-button type="danger" :disabled="!canReview" @click="review('rejected')">退回当前成片</el-button><el-button :disabled="!canReview" @click="review('pending')">撤回当前审核</el-button><el-button @click="move(1)" :disabled="position>=visibleIds.length-1||cohort!==state.cohort">跳过 / 下一张</el-button><el-button :disabled="locked||detail.status!=='approved'" @click="deliver">生成单人交付包</el-button></div>
   <p class="muted">处理成功只进入待审核。审核操作即时保存，关闭窗口不等于交付。</p><h3>{{preview?'试处理阶段结果（非正式成片）':'正式处理阶段结果'}}</h3><Photo v-if="preview?.artifact" :artifact="preview.artifact" label="试处理成片"/><Stages :result="preview||result"/>
   <el-card v-for="b in batches" :key="b.id" shadow="never"><p>交付记录 {{b.id.slice(0,8)}} · {{b.status}} · {{b.items.length}} 人</p><el-button v-if="!b.historical&&b.status!=='cancelled'" tag="a" :href="url('deliveries/'+b.id+'/download',cohort)">下载交付包</el-button><el-button v-if="b.status==='prepared'&&b.items.length===1" :disabled="locked" @click="confirm(b)">确认已交付</el-button><p v-else-if="b.status==='prepared'">多人交付包请在照片交付页面统一确认。</p></el-card>
   <el-collapse><el-collapse-item title="版本与操作记录"><pre data-testid="version-history">{{JSON.stringify({history:detail.history,versions:detail.results,delivered:detail.delivered},null,2)}}</pre></el-collapse-item></el-collapse>
  </template>
 </el-dialog>
</template>
