<script setup>
import {ref,onMounted} from 'vue';
import {ElMessage,ElMessageBox} from 'element-plus';
import {api} from '../api';
import {state,loadCohorts} from '../workspace';
const input=ref(''),cohorts=ref([]),trash=ref([]),dirty=ref(false),busy=ref(false);
async function load(){const d=await api('cohorts');cohorts.value=d.cohorts.map(name=>({name,archived:d.records?.[name]?.archived||false}));trash.value=await api('recycle-bin');}
function add(){const y=input.value.trim();if(!/^\d{4}$/.test(y))throw Error('请输入四位年份');if(cohorts.value.some(x=>x.name===y+'级'))throw Error('届次已存在');cohorts.value.push({name:y+'级',archived:false});input.value='';dirty.value=true;}
async function save(){busy.value=true;try{await api('cohort-settings',{cohorts:cohorts.value.filter(x=>!x.archived).map(x=>x.name)},'PUT');await loadCohorts();await load();dirty.value=false;ElMessage.success('届次已保存');}finally{busy.value=false;}}
async function remove(c){await ElMessageBox.confirm('删除已保存届次前会检查业务数据，确认继续？','删除届次');cohorts.value=cohorts.value.filter(x=>x!==c);dirty.value=true;}
onMounted(load);
</script>
<template><el-card shadow="never"><template #header><h3>届次管理</h3></template><p class="muted">届次按年份保存，修改不会改变内部关联数据。删除已保存届次需要保存时再次校验。</p><div class="tag-editor"><el-tag v-for="c in cohorts" :key="c.name" closable @close="remove(c)">{{c.name}}</el-tag><el-input v-model="input" size="small" placeholder="输入年份，如 2027" @keyup.enter="add"/><el-button size="small" @click="add">添加</el-button></div><el-alert v-if="dirty" title="有未保存的届次修改" type="warning" :closable="false"/><el-button type="primary" :loading="busy" @click="save">保存届次</el-button></el-card><el-card shadow="never"><template #header><h3>数据回收站</h3></template><el-empty v-if="!trash.length" description="回收站为空"/><el-table v-else :data="trash"><el-table-column prop="student.id" label="学号"/><el-table-column prop="deleted_at" label="移入时间"/></el-table></el-card></template>
