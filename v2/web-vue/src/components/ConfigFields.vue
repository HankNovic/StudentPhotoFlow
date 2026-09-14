<script setup>
import { ref } from 'vue';
import { ElMessage } from 'element-plus';
import { names,groups,choices,engineNames } from '../config';
import { state } from '../workspace';
import { api } from '../api';
import HivisionUrlPicker from './HivisionUrlPicker.vue';
const props=defineProps({modelValue:Object}),emit=defineEmits(['update:modelValue']);
const testResult=ref(''),testing=ref(false);
function set(key,value){emit('update:modelValue',{...props.modelValue,[key]:value});}
async function add(){state.urls=await api('engines/hivision/urls',{url:props.modelValue.hivision_url});ElMessage.success('地址已保存到历史');}
async function test(){testing.value=true;try{testResult.value=JSON.stringify(await api('engines/hivision/test',{url:props.modelValue.hivision_url}),null,2);}finally{testing.value=false;}}
</script>
<template>
 <el-form label-position="top">
  <el-card v-for="[title,help,keys] in groups" :key="title" shadow="never" class="config-group">
   <template #header><h3>{{title}}</h3><p class="muted">{{help}}</p></template>
   <div class="config-grid">
    <el-form-item v-for="key in keys" :key="key" :label="names[key]" :data-config="key">
     <HivisionUrlPicker v-if="key==='hivision_url'" :model-value="modelValue[key]" @update:model-value="set(key,$event)"/>
     <el-select v-else-if="choices[key]" :model-value="modelValue[key]" @update:model-value="set(key,$event)" :aria-label="names[key]">
      <el-option v-for="value in choices[key]" :key="value" :value="value" :label="engineNames[value]||value"/>
     </el-select>
     <el-switch v-else-if="typeof modelValue[key]==='boolean'" :model-value="modelValue[key]" @update:model-value="set(key,$event)" :aria-label="names[key]"/>
     <el-input-number v-else-if="typeof modelValue[key]==='number'" :model-value="modelValue[key]" @update:model-value="set(key,$event)" :aria-label="names[key]" :step="Number.isInteger(modelValue[key])?1:0.01"/>
     <el-input v-else :model-value="modelValue[key]" @update:model-value="set(key,$event)" :aria-label="names[key]"/>
    </el-form-item>
   </div>
   <template v-if="keys.includes('hivision_url')">
    <div class="toolbar"><el-button @click="add">添加当前地址到历史</el-button><el-button @click="test" :loading="testing">测试当前 API（不上传照片）</el-button></div>
    <p class="muted">输入新地址后按回车确认。展开可选择历史，× 仅删除历史记录。切换后保存配置供下次运行使用。</p>
    <pre v-if="testResult" data-testid="engine-test-result">{{testResult}}</pre>
   </template>
  </el-card>
 </el-form>
</template>