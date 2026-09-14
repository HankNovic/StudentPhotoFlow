<script setup>
import Photo from './Photo.vue';
defineProps({result:Object});
const status={passed:'通过',disabled:'未启用',adjusted:'已调整',rejected:'未通过',skipped:'已跳过',success:'完成'};
</script>
<template>
 <div v-if="result" class="stages">
  <el-card v-for="(step,i) in result.stages||[]" :key="i" shadow="never">
   <h4>{{step.label}}</h4><Photo :artifact="step.artifact" :label="step.label"/><el-tag>{{status[step.status]||step.status}}</el-tag><p>{{step.detail}}</p>
   <el-collapse><el-collapse-item title="指标"><pre>{{JSON.stringify(step.metrics,null,2)}}</pre></el-collapse-item></el-collapse>
  </el-card>
 </div>
 <el-empty v-else description="尚无处理结果"/>
</template>