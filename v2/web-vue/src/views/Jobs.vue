<script setup>
import {state,refresh} from '../workspace';
import {api} from '../api';
import {ElMessage,ElMessageBox} from 'element-plus';
import {ref,reactive} from 'vue';
const collapsed=reactive({});
function toggleErrors(id){collapsed[id]=!collapsed[id];}
const busy=ref('');
const labels={running:'运行中',pausing:'正在暂停',paused:'已暂停',cancelling:'正在结束',cancelled:'任务已完成（手动结束）',interrupted:'系统中断，可核对后恢复',completed:'已结束'};
async function action(job,value){const cid=state.cohort;if(value==='cancel')await ElMessageBox.confirm('当前正在处理的学生完成后结束任务，其余项目不再执行，本任务结束后不可继续。','安全结束',{type:'warning',confirmButtonText:'安全结束',cancelButtonText:'取消',confirmButtonClass:'el-button--danger'});busy.value=job.id;try{await api('jobs/'+job.id+'/'+value,{},undefined,cid);await refresh();ElMessage.success('操作已提交');}finally{busy.value='';}}
async function reconcile(job,sid,decision){const cid=state.cohort;await ElMessageBox.confirm(decision==='retry'?'请先核对服务端和本地结果。确认没有可用结果并允许重新调用外部服务？':'此项不再执行，不计为成功；后续如需处理请创建新任务。','核对中断项目',{confirmButtonText:'已核对，确认',type:'warning'});await api('jobs/'+job.id+'/reconcile/'+encodeURIComponent(sid),{confirmed:true,decision},undefined,cid);await refresh();}
</script>
<template>
 <p class="muted">当前 {{state.cohortName}} 的任务。暂停在当前学生完成后生效；安全结束是不可继续的终态。页面刷新或切换不会停止任务。</p>
 <el-empty v-if="!state.jobs.length" description="暂无任务"/>
 <el-card v-for="job in state.jobs" :key="job.id" shadow="never" data-testid="job" :data-job="job.id">
  <div class="section-heading"><h3>{{job.kind==='import'?'原图接收':'照片处理'}} · {{job.id.slice(0,8)}}</h3><el-tag>{{labels[job.status]||job.status}}{{job.status==='completed'&&job.counts?.failed?'，失败 '+job.counts.failed+' 人':''}}</el-tag></div>
  <p>成功 {{job.counts?.success||0}} · 失败 {{job.counts?.failed||0}} · 未执行 {{(job.counts?.remaining||0)+(job.counts?.skipped||0)}} · 总计 {{job.counts?.total||0}} · 当前 {{job.current||'—'}}</p>
  <el-progress :percentage="Math.min(100,Math.round(((job.counts?.success||0)+(job.counts?.failed||0))/Math.max(1,job.counts?.total||0)*100))"/>
  <div class="toolbar"><el-button v-if="job.status==='running'" :loading="busy===job.id" @click="action(job,'pause')">暂停</el-button><el-button v-if="['paused','interrupted'].includes(job.status)" :disabled="Object.keys(job.uncertain||{}).length>0" :loading="busy===job.id" @click="action(job,'resume')">继续未完成项</el-button><el-button v-if="['running','pausing','paused','interrupted'].includes(job.status)" type="danger" :loading="busy===job.id" @click="action(job,'cancel')">安全结束</el-button></div>
  <el-alert v-if="job.message" :title="job.message" type="error" :closable="false"/>
  <div v-if="Object.keys(job.errors||{}).length" class="job-error" data-testid="job-errors"><div class="section-heading"><h4>失败 {{Object.keys(job.errors).length}} 人</h4><el-button link @click="toggleErrors(job.id)">{{collapsed[job.id]?'展开失败详情':'收起失败详情'}}</el-button></div><template v-if="!collapsed[job.id]"><el-card v-for="(error,sid) in job.errors" :key="sid" shadow="never"><strong>学号 {{sid}}</strong><p>{{String(error).slice(0,400)}}</p><details v-if="String(error).length>400"><summary>完整错误原因</summary>{{error}}</details></el-card></template></div>
  <el-card v-for="(reason,sid) in job.uncertain||{}" :key="sid" shadow="never"><h4>学号 {{sid}} · 结果需核对</h4><p>{{reason}}</p><template v-if="job.status==='interrupted'"><el-button @click="reconcile(job,sid,'retry')">核对后允许重试</el-button><el-button @click="reconcile(job,sid,'skip')">核对后不再执行</el-button></template><template v-else><p>任务已手动结束，不可继续。请核对外部结果后归档此项，后续处理须另建任务。</p><el-button @click="reconcile(job,sid,'skip')">核对后归档未执行项</el-button></template></el-card>
  <el-collapse><el-collapse-item title="处理计划"><pre>{{JSON.stringify(job.plan.items,null,2)}}</pre></el-collapse-item></el-collapse>
 </el-card>
</template>
