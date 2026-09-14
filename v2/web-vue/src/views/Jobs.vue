<script setup>
import { state,refresh } from '../workspace';
import { api } from '../api';
import { ElMessage } from 'element-plus';
const labels={running:'运行中',pausing:'正在暂停',paused:'已暂停',cancelling:'正在中断',cancelled:'已中断',interrupted:'异常中断，可恢复',completed:'已结束'};
async function action(job,value){await api('jobs/'+job.id+'/'+value,{});await refresh();ElMessage.success('操作已提交');}
</script>
<template>
 <p class="muted">显示整个工作区任务。暂停和中断在当前学生处理结束后生效；结束不代表全部照片成功。</p>
 <el-empty v-if="!state.jobs.length" description="暂无任务"/>
 <el-card v-for="job in state.jobs" :key="job.id" shadow="never" data-testid="job">
  <div class="section-heading"><h3>{{job.kind==='import'?'原图接收':'照片处理'}} · {{job.id.slice(0,8)}}</h3><el-tag>{{labels[job.status]||job.status}}</el-tag></div>
  <p>完成 {{job.completed.length}} / {{job.plan.items.filter(x=>x.execute).length}} · 当前 {{job.current||'—'}} · 失败 {{Object.keys(job.errors).length}}</p>
  <el-progress :percentage="Math.min(100,Math.round(job.completed.length/Math.max(1,job.plan.items.filter(x=>x.execute).length)*100))"/>
  <div class="toolbar"><el-button @click="action(job,'pause')">暂停</el-button><el-button @click="action(job,'resume')">继续未完成项</el-button><el-button @click="action(job,'cancel')">安全中断</el-button></div>
  <el-alert v-if="job.message" :title="job.message" type="warning"/>
  <el-collapse><el-collapse-item title="错误和计划"><pre>{{JSON.stringify({errors:job.errors,plan:job.plan.items},null,2)}}</pre></el-collapse-item></el-collapse>
 </el-card>
</template>