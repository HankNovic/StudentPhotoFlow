<script setup>
import { ref,computed,watch,onMounted,onUnmounted } from 'vue';
import { ElMessage,ElMessageBox } from 'element-plus';
import { api,labels,run } from '../api';
import { state,refresh } from '../workspace';
import Photo from './Photo.vue';
import Stages from './Stages.vue';
import ConfigFields from './ConfigFields.vue';
const props=defineProps({id:String,visibleIds:Array}),emit=defineEmits(['update:id']);
const detail=ref(null),preview=ref(null),busy=ref(false),settings=ref([]);
const source=computed(()=>detail.value?.sources.at(-1));
const result=computed(()=>detail.value?.results.filter(r=>r.source_id===source.value?.id).at(-1));
const canReview=computed(()=>!!result.value?.artifact&&!detail.value?.status.startsWith('delivered'));
const position=computed(()=>props.visibleIds.indexOf(props.id));
async function load(){if(props.id){detail.value=await api('students/'+encodeURIComponent(props.id));preview.value=null;}}
watch(()=>props.id,()=>run(load),{immediate:true});
watch(()=>state.cohort,()=>emit('update:id',''));
async function review(decision){
 let reason='';
 if(decision==='rejected')reason=(await ElMessageBox.prompt('退回原因（可留空）','退回当前成片',{confirmButtonText:'保存',cancelButtonText:'取消'})).value||'';
 await api('reviews',{student_id:props.id,result_id:result.value.id,decision,expected_revision:detail.value.revision,reason});
 await load();await refresh();ElMessage.success('审核已保存');
}
function move(delta){const next=props.visibleIds[position.value+delta];if(next)emit('update:id',next);}
async function test(){
 busy.value=true;try{preview.value=await api('students/'+encodeURIComponent(props.id)+'/preview',state.config);ElMessage.success('试处理完成，未改变正式成片与审核状态');}finally{busy.value=false;}
}
async function replace(){
 const {value}=await ElMessageBox.prompt('保留历史交付事实，请填写替换原因','启动照片替换',{inputValidator:v=>!!v?.trim()||'请填写原因',confirmButtonText:'启动替换',cancelButtonText:'取消'});
 await api('students/'+encodeURIComponent(props.id)+'/replacement',{reason:value});await load();await refresh();ElMessage.success('替换流程已启动，可上传新照片或重新处理');
}
async function save(){state.config=await api('config',state.config,'PUT');ElMessage.success('预览参数已保存供批量处理');}
function key(e){if(!props.id||['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName))return;if(e.key==='ArrowLeft')move(-1);if(e.key==='ArrowRight')move(1);}
onMounted(()=>document.addEventListener('keydown',key));onUnmounted(()=>document.removeEventListener('keydown',key));
</script>
<template>
 <el-dialog :model-value="!!id" @update:model-value="emit('update:id','')" :title="'学生 '+id+' · 流程与审核'" width="min(1120px,96vw)" :close-on-click-modal="true" destroy-on-close>
  <template v-if="detail">
   <div class="toolbar"><el-tag>{{labels[detail.status]||detail.status}}</el-tag><span class="muted">修订号 {{detail.revision}}</span></div>
   <div class="comparison"><Photo :artifact="source" label="原始照片"/><Photo :artifact="result?.artifact" label="当前成片"/></div>
   <div class="toolbar">
    <el-button @click="move(-1)" :disabled="position<=0">上一张</el-button>
    <el-button type="success" :disabled="!canReview" @click="review('approved')">审核通过当前成片</el-button>
    <el-button type="danger" :disabled="!canReview" @click="review('rejected')">退回当前成片</el-button>
    <el-button :disabled="!canReview" @click="review('pending')">撤回当前审核</el-button>
    <el-button @click="move(1)" :disabled="position>=visibleIds.length-1">跳过 / 下一张</el-button>
    <el-button @click="test" :loading="busy">用当前配置试处理</el-button><el-button @click="replace">启动替换流程</el-button>
   </div>
   <p class="muted">审核操作即时保存。点击遮罩关闭；跳过只切换照片，不修改状态。点击图片查看原尺寸。</p>
   <el-collapse v-model="settings"><el-collapse-item name="settings" title="调整处理参数，再次预览"><ConfigFields v-model="state.config"/><el-button @click="save">保存这些参数供批量处理</el-button></el-collapse-item></el-collapse>
   <h3>{{preview?'试处理阶段结果（非正式成片）':'正式处理阶段结果'}}</h3>
   <el-alert v-if="preview" type="info" :closable="false" title="以下是单张试处理结果，不改变正式成片和审核状态。"/>
   <Photo v-if="preview?.artifact" :artifact="preview.artifact" label="试处理成片"/>
   <Stages :result="preview||result"/>
   <el-collapse><el-collapse-item title="版本与操作记录"><pre data-testid="version-history">{{JSON.stringify({history:detail.history,versions:detail.results,delivered:detail.delivered},null,2)}}</pre></el-collapse-item></el-collapse>
  </template>
 </el-dialog>
</template>