<script setup>
import {ref,computed,watch,onBeforeUnmount} from 'vue';
import {ElMessage} from 'element-plus';
import {api} from '../api';
import {state} from '../workspace';
import PagedTable from './PagedTable.vue';
const emit=defineEmits(['generated','busy']);
const open=ref(false),profiles=ref([]),profileId=ref(''),plan=ref(null),loading=ref(false),generating=ref(false),error=ref(''),cid=ref(''),ids=ref([]),cohortName=ref('');
let sequence=0;
const signature=p=>JSON.stringify({profile:p.profile_snapshot,items:p.items});
const available=computed(()=>profiles.value.filter(p=>p.status==='active'));
const allowed=computed(()=>!!plan.value?.ok&&plan.value.profile_id===profileId.value&&!loading.value&&!generating.value&&!error.value);
watch(generating,v=>emit('busy',v),{flush:'sync'});
function close(){if(generating.value)return;sequence++;loading.value=false;open.value=false;plan.value=null;}
watch(()=>state.cohort,()=>{if(open.value&&!generating.value)close();});
onBeforeUnmount(()=>sequence++);
async function preview(){
 const seq=++sequence,selected=profileId.value;plan.value=null;error.value='';loading.value=true;
 try{
  if(!selected)throw Error('当前届次没有启用的导出格式，请先在照片交付中设置。');
  const data=await api('deliveries/preview',{student_ids:ids.value,profile_id:selected},undefined,cid.value);
  if(seq===sequence&&open.value)plan.value=data;
 }catch(e){if(seq===sequence&&open.value)error.value=e.message||String(e);}
 finally{if(seq===sequence)loading.value=false;}
}
async function show(cohort,students){
 if(open.value||generating.value)return;
 if(!cohort||!students.length)return;
 cid.value=cohort;ids.value=[...students];cohortName.value=state.allCohorts.find(c=>c.id===cohort)?.name||cohort;
 profiles.value=[];profileId.value='';plan.value=null;error.value='';open.value=true;loading.value=true;
 const seq=++sequence;
 try{
  const data=await api('export-profiles',undefined,undefined,cohort);
  if(seq!==sequence||!open.value)return;
  profiles.value=data;profileId.value=available.value.find(p=>p.is_default)?.id||available.value[0]?.id||'';
  await preview();
 }catch(e){if(seq===sequence&&open.value){error.value=e.message||String(e);loading.value=false;}}
}
async function confirm(){
 if(!allowed.value)return;
 generating.value=true;error.value='';
 const selected=profileId.value,cohort=cid.value,students=[...ids.value],previous=plan.value;
 try{
  // Recheck persisted data before submission; changed preview needs another confirmation.
  const checked=await api('deliveries/preview',{student_ids:students,profile_id:selected},undefined,cohort);
  if(!checked.ok||signature(checked)!==signature(previous)){
   plan.value=checked;ElMessage.warning('学生或格式已变化，请核对新的文件名后再次确认。');return;
  }
  const batch=await api('deliveries',{student_ids:students,profile_id:selected},undefined,cohort);
  open.value=false;sequence++;plan.value=null;
  emit('generated',batch,{cid:cohort,ids:students});
 }catch(e){error.value=e.message||String(e);plan.value=null;}
 finally{generating.value=false;}
}
function showRetry(){const cohort=cid.value,students=[...ids.value];close();show(cohort,students);}
defineExpose({show,close});
</script>
<template>
 <el-dialog :model-value="open" @update:model-value="close" title="选择导出格式并预览文件名" width="min(900px,94vw)" top="16px" class="delivery-preview-dialog" append-to-body :close-on-click-modal="!generating" :close-on-press-escape="!generating" :show-close="!generating">
  <p class="muted">所属届次：{{cohortName}} · 已选 {{ids.length}} 人；预览不会生成交付包。</p>
  <el-select v-model="profileId" aria-label="导出格式" :disabled="generating||!profiles.length" @change="preview">
   <el-option v-for="p in available" :key="p.id" :label="p.name+' · '+p.template+' · v'+p.revision" :value="p.id"/>
  </el-select>
  <p v-if="loading" role="status" class="muted">正在检查文件名…</p>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  <el-alert v-else-if="plan&&!plan.ok" title="文件名检查未通过，请切换格式或修改名单字段" type="error" :closable="false"/>
  <PagedTable v-show="!!plan" :items="plan?.items||[]"><el-table-column prop="student_id" label="学号"/><el-table-column prop="file_name" label="实际文件名"/><el-table-column prop="reason" label="状态"/></PagedTable>
  <template #footer><el-button :disabled="generating" @click="close">取消</el-button><el-button v-if="error" :disabled="loading||generating" @click="showRetry">重新加载格式</el-button><el-button type="primary" :disabled="!allowed" :loading="generating" @click="confirm">确认生成交付包</el-button></template>
 </el-dialog>
</template>
<style>
.delivery-preview-dialog{max-height:calc(100dvh - 32px);display:flex;flex-direction:column;margin-bottom:16px}
.delivery-preview-dialog .el-dialog__header,.delivery-preview-dialog .el-dialog__footer{flex:none}
.delivery-preview-dialog .el-dialog__body{min-height:0;overflow:auto}
.delivery-preview-dialog .el-alert{margin-top:8px}
</style>
