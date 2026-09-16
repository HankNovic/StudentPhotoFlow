<script setup>
import { ref,computed,watch } from 'vue';
import { ElMessage,ElMessageBox } from 'element-plus';
import { state,refresh } from '../workspace';
import { api,labels,download } from '../api';
import Photo from '../components/Photo.vue';
import RecycleDialog from '../components/RecycleDialog.vue';
import StudentDetail from '../components/StudentDetail.vue';
const emit=defineEmits(['navigate']);
const recycle=ref();
async function single(){if(!state.cohort)throw Error('请先选择届次');const cid=state.cohort;const {value}=await ElMessageBox.prompt('在 '+state.cohortName+' 中精确查找已有学号，不会新建学生','单人补录',{inputPlaceholder:'完整学号'});const sid=value.trim();await api('students/'+encodeURIComponent(sid),undefined,undefined,cid);if(cid!==state.cohort)throw Error('届次已切换，请重新确认学生');detailId.value=sid;}
const q=ref(''),status=ref(''),selected=ref([]),detailId=ref(''),batchDecision=ref(''),reason=ref('');
const planOpen=ref(false),planData=ref(null),newVersion=ref(false),planIds=ref([]),busy=ref(false);
const filtered=computed(()=>state.students.filter(s=>(!status.value||s.status===status.value)&&s.id.includes(q.value.trim())));
const visibleIds=computed(()=>filtered.value.map(s=>s.id));
const all=computed(()=>filtered.value.length>0&&filtered.value.every(s=>selected.value.includes(s.id)));
watch([q,status,()=>state.cohort],()=>{selected.value=[];planOpen.value=false;});
watch(()=>state.students,()=>{selected.value=selected.value.filter(id=>visibleIds.value.includes(id));});
watch(()=>[newVersion.value,JSON.stringify(state.config)],()=>{planData.value=null;});
function toggle(id,value){selected.value=value?[...new Set([...selected.value,id])]:selected.value.filter(x=>x!==id);}
function toggleAll(value){selected.value=value?[...visibleIds.value]:[];}
function batch(decision){if(!state.students.some(s=>selected.value.includes(s.id)&&s.status==='review'))throw Error('请先选中待人工审核学生');batchDecision.value=decision;reason.value='';}
async function applyBatch(){
 const student_ids=state.students.filter(s=>selected.value.includes(s.id)&&s.status==='review').map(s=>s.id);
 busy.value=true;try{const d=await api('reviews/batch',{student_ids,decision:batchDecision.value,reason:reason.value});batchDecision.value='';selected.value=[];await refresh();ElMessage.success('已批量审核 '+d.count+' 人');}finally{busy.value=false;}
}
async function replan(){const cid=state.cohort;const data=await api('processing-jobs',{student_ids:planIds.value,config:state.config,new_version:newVersion.value,dry_run:true},undefined,cid);if(cid!==state.cohort)return false;planData.value=data;return true;}
async function openPlan(){if(!selected.value.length)throw Error('请先选中学生');planIds.value=[...selected.value];if(await replan())planOpen.value=true;}
async function start(){
 busy.value=true;try{await api('processing-jobs',{student_ids:planIds.value,config:planData.value.config,new_version:newVersion.value,dry_run:false,expected_revision:planData.value.revision});planOpen.value=false;await refresh();emit('navigate','jobs');ElMessage.success('处理任务已创建');}finally{busy.value=false;}
}
async function deliver(){
 if(!selected.value.length)throw Error('请选择审核通过的学生');
 await api('deliveries',{student_ids:selected.value});await refresh();emit('navigate','deliveries');ElMessage.success('交付包已生成，实际发送后再确认交付');
}
</script>
<template>
 <div class="toolbar"><el-button type="primary" @click="single" :disabled="!state.cohort||state.archived">单人补录</el-button><el-button type="danger" :disabled="!selected.length||state.archived" @click="recycle.show(state.cohort,selected)">选中学生移入回收站</el-button></div>
 <div class="counts"><el-button v-for="(label,key) in labels" :key="key" @click="status=key" :class="{chosen:status===key}"><span>{{label}}</span><b>{{state.students.filter(s=>s.status===key).length}}</b></el-button></div>
 <el-card shadow="never">
  <div class="toolbar">
   <el-input v-model="q" placeholder="搜索学号" aria-label="搜索学号" clearable class="search"/>
   <el-select v-model="status" :empty-values="[null,undefined]" aria-label="状态筛选" class="filter"><el-option label="全部状态" value=""/><el-option v-for="(label,key) in labels" :key="key" :label="label" :value="key"/></el-select>
   <el-checkbox :model-value="all" @update:model-value="toggleAll">全选筛选结果</el-checkbox><span data-testid="selection-count">已选 {{selected.length}} 人</span>
  </div>
  <div class="toolbar">
   <el-button @click="batch('approved')">批量审核通过</el-button><el-button @click="batch('rejected')">批量退回</el-button>
   <el-button type="primary" @click="openPlan">预览处理计划</el-button><el-button @click="deliver">生成选中照片交付包</el-button>
   <el-button @click="download('选中学号.json',{student_ids:selected})">导出选中名单</el-button>
  </div>
  <el-table :data="filtered" row-key="id" empty-text="当前届次暂无学生，请前往数据接入" data-testid="student-table">
   <el-table-column width="56"><template #default="{row}"><el-checkbox :model-value="selected.includes(row.id)" @update:model-value="toggle(row.id,$event)" :aria-label="'选择学生 '+row.id"/></template></el-table-column>
   <el-table-column prop="id" label="学号" min-width="140"/>
   <el-table-column label="照片" width="150"><template #default="{row}"><Photo :artifact="row.result?.artifact||row.source" :label="row.id"/></template></el-table-column>
   <el-table-column label="状态" min-width="150"><template #default="{row}"><el-tag>{{labels[row.status]||row.status}}</el-tag></template></el-table-column>
   <el-table-column label="操作" min-width="180"><template #default="{row}"><el-button link type="primary" @click="detailId=row.id">查看流程 / 审核</el-button><el-button v-if="row.status==='missing'" link :disabled="state.archived" @click="detailId=row.id">补交照片</el-button><el-button link type="danger" :disabled="state.archived" @click="recycle.show(state.cohort,[row.id])">移入回收站</el-button></template></el-table-column>
  </el-table>
 </el-card>
 <RecycleDialog ref="recycle"/>
 <StudentDetail v-model:id="detailId" :visible-ids="visibleIds"/>
 <el-dialog :model-value="!!batchDecision" @update:model-value="batchDecision=''" :title="batchDecision==='approved'?'批量审核通过':'批量退回'" width="440px">
  <p>仅处理当前选中的待人工审核学生。</p><el-input v-model="reason" type="textarea" placeholder="备注（可留空）"/>
  <template #footer><el-button @click="batchDecision=''">取消</el-button><el-button type="primary" @click="applyBatch" :loading="busy">确认批量审核</el-button></template>
 </el-dialog>
 <el-dialog v-model="planOpen" title="本次执行计划" width="min(850px,94vw)">
  <el-checkbox v-model="newVersion">明确生成新处理版本（仍遵守已交付保护）</el-checkbox>
  <el-button @click="replan">重新计算计划</el-button>
  <p v-if="planData">执行 {{planData.items.filter(x=>x.execute).length}} 人，跳过 {{planData.items.filter(x=>!x.execute).length}} 人</p>
  <el-alert v-else title="参数或范围已变化，请重新计算计划" type="warning" :closable="false"/>
  <el-table :data="planData?.items||[]"><el-table-column prop="student_id" label="学号"/><el-table-column label="执行情况"><template #default="{row}">{{row.execute?'将处理':row.reason}}</template></el-table-column></el-table>
  <template #footer><el-button @click="planOpen=false">取消</el-button><el-button type="primary" :disabled="!planData" :loading="busy" @click="start">按此计划开始处理</el-button></template>
 </el-dialog>
</template>
