<script setup>
import {ref,computed,onMounted} from 'vue';
import {ElMessage,ElMessageBox} from 'element-plus';
import {api} from '../api';
import {loadCohorts,changeCohort,refresh} from '../workspace';
import RecycleDialog from '../components/RecycleDialog.vue';
const input=ref(''),records=ref([]),trash=ref([]),audit=ref([]),revision=ref(0),days=ref(30),automatic=ref(true),baseline=ref(''),busy=ref(false),editing=ref(null),editValue=ref(''),archived=ref(false),deleted=ref([]),recycle=ref();
const serialized=computed(()=>JSON.stringify({cohorts:records.value,retention_days:automatic.value?days.value:null}));
const dirty=computed(()=>serialized.value!==baseline.value);
async function load(){const d=await api('settings');records.value=d.cohorts;revision.value=d.revision;automatic.value=d.retention_days!==null;days.value=d.retention_days||30;audit.value=d.audit;deleted.value=[];baseline.value=serialized.value;trash.value=await api('recycle-bin');}
function year(value,except){const y=value.trim();if(!/^[1-9][0-9]{3}$/.test(y))throw Error('请输入 1000–9999 的四位年份');if(records.value.some(c=>c!==except&&c.year===y))throw Error('届次已存在（包含归档届次）');return y;}
function add(){const y=year(input.value);records.value.push({year:y,name:y+'级',archived:false});input.value='';}
function edit(c){editing.value=c;editValue.value=c.year;}
function confirmEdit(){const y=year(editValue.value,editing.value);Object.assign(editing.value,{year:y,name:y+'级'});editing.value=null;}
async function remove(c){if(c.id){await ElMessageBox.confirm('确认删除 '+c.name+'？有学生、照片、任务、交付或回收站数据时服务器会拒绝。','删除已保存届次',{type:'warning'});deleted.value.push(c.id);}records.value=records.value.filter(x=>c.id?x.id!==c.id:x!==c);}
async function save(){if(editing.value)confirmEdit();busy.value=true;try{await api('settings',{cohorts:records.value,retention_days:automatic.value?days.value:null,revision:revision.value,confirmed_deletions:deleted.value},'PUT');await loadCohorts();await refresh();await load();ElMessage.success('系统设置已保存');}finally{busy.value=false;}}
async function action(t,mode){if(mode==='purge')await ElMessageBox.prompt('将永久删除 '+t.cohort+' 的 '+t.students.length+' 名学生及无共享引用的照片。输入“永久删除”。','永久删除',{inputValidator:v=>v==='永久删除'||'请输入永久删除'});await api('recycle-bin/'+t.cohort_id+'/'+t.id+'/'+mode,{confirmation:'永久删除'});await load();await refresh();ElMessage.success(mode==='restore'?'已恢复原业务状态':'永久清理完成');}
async function view(c){await changeCohort(c.id);ElMessage.success('已选中 '+c.name+(c.archived?'（只读）':''));}
onMounted(load);
</script>
<template>
 <el-card shadow="never" :inert="busy ? '' : null"><template #header><h3>届次管理</h3><p class="muted">点击标签年份编辑；Enter 确认，Esc 取消。所有编辑统一保存。</p></template>
  <div class="tag-editor" role="group" aria-label="届次标签编辑器"><template v-for="(c,index) in records" :key="c.id||index"><el-input v-if="editing===c" v-model="editValue" aria-label="编辑届次年份" @keyup.enter="confirmEdit" @keyup.esc="editing=null"/><el-tag v-else closable @close="remove(c)"><button class="tag-name" @click="edit(c)">{{c.name}}{{c.archived?' · 归档':''}}</button></el-tag></template><el-input v-model="input" aria-label="新增届次年份" placeholder="四位年份" @keyup.enter="add"/><el-button @click="add">添加</el-button></div>
  <el-checkbox v-model="archived">显示归档届次</el-checkbox>
  <el-table :data="records.filter(c=>archived||!c.archived)"><el-table-column prop="name" label="届次"/><el-table-column label="业务数据"><template #default="{row}">{{row.blockers||'新届次'}}</template></el-table-column><el-table-column label="操作" min-width="300"><template #default="{row}"><el-button @click="row.archived=!row.archived">{{row.archived?'恢复届次':'归档届次'}}</el-button><el-button v-if="row.id" @click="view(row)">查看数据</el-button><el-button v-if="row.id" type="danger" :disabled="dirty||row.archived" @click="recycle.show(row.id,[],true)">清空本届数据</el-button></template></el-table-column></el-table>
  <h4 class="spaced">回收站保留规则</h4><el-switch v-model="automatic" aria-label="启用到期自动清理" active-text="启用到期自动清理"/><el-input-number v-model="days" :min="1" :max="3650" :disabled="!automatic" aria-label="回收站保留天数"/><span>天</span><p class="muted">默认 30 天。修改天数仅影响之后移入的数据；关闭自动清理暂停到期清理。已有到期时间不被重置，重新启用按原到期时间补清理。</p>
  <el-alert v-if="dirty" title="有未保存的系统设置" type="warning" :closable="false"/><div class="toolbar"><el-button type="primary" :loading="busy" :disabled="!dirty" @click="save">保存系统设置</el-button><el-button @click="load">重新加载</el-button></div>
 </el-card>
 <el-card shadow="never"><template #header><h3>数据回收站 · {{trash.reduce((n,t)=>n+t.students.length,0)}} 人</h3></template><el-table :data="trash" empty-text="回收站为空"><el-table-column prop="cohort" label="所属届次"/><el-table-column label="范围"><template #default="{row}">{{row.students.length}} 人 / {{row.photos}} 照片 / {{row.deliveries}} 交付关联<el-collapse><el-collapse-item title="查看学号">{{row.students.join('、')}}</el-collapse-item></el-collapse></template></el-table-column><el-table-column prop="deleted_at" label="删除时间"/><el-table-column label="预计永久清理"><template #default="{row}">{{row.expires_at||'不自动清理'}}<p v-if="row.error">{{row.error}}</p></template></el-table-column><el-table-column label="操作"><template #default="{row}"><el-button :disabled="row.status!=='recoverable'" @click="action(row,'restore')">恢复</el-button><el-button type="danger" @click="action(row,'purge')">{{row.status==='purging'?'重试清理':'永久删除'}}</el-button></template></el-table-column></el-table></el-card>
 <el-collapse><el-collapse-item title="系统操作记录"><pre>{{JSON.stringify(audit,null,2)}}</pre></el-collapse-item></el-collapse>
 <RecycleDialog ref="recycle" @changed="load"/>
</template>
