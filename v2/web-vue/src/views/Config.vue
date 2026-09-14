<script setup>
import { ElMessage } from 'element-plus';
import { state } from '../workspace';
import { api,download,run } from '../api';
import ConfigFields from '../components/ConfigFields.vue';
async function save(){state.config=await api('config',state.config,'PUT');ElMessage.success('运行配置已保存');}
async function importConfig(file){state.config=await api('config',JSON.parse(await file.raw.text()),'PUT');ElMessage.success('配置已导入并保存');}
</script>
<template>
 <div class="toolbar sticky-tools"><el-button type="primary" @click="save">保存配置</el-button><el-button @click="download('StudentPhotoFlow配置.json',state.config)">导出配置 JSON</el-button><el-upload :auto-upload="false" :show-file-list="false" accept=".json" :on-change="file=>run(()=>importConfig(file))"><el-button>导入配置 JSON</el-button></el-upload></div>
 <p class="muted">当前参数供单张试处理与处理计划共用。保存参数不会自动重新处理照片。</p>
 <ConfigFields v-model="state.config"/>
</template>