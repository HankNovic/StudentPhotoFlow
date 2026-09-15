<script setup>
import {ref} from 'vue';
import {ElMessage} from 'element-plus';
import {api} from '../api';
import {refresh} from '../workspace';
const emit=defineEmits(['changed']);
const open=ref(false),info=ref(null),confirmation=ref(''),busy=ref(false);
let cid='',whole=false;
async function show(cohort,studentIds=[],all=false){cid=cohort;whole=all;info.value=await api('recycle-bin/'+cid+'/preview',{student_ids:studentIds,all});confirmation.value='';open.value=true;}
async function move(){busy.value=true;try{await api('recycle-bin/'+cid+'/move',{student_ids:info.value.student_ids,all:whole,revision:info.value.revision,confirmed:true,confirmation:confirmation.value});open.value=false;await refresh();emit('changed');ElMessage.success('完整数据已移入回收站');}finally{busy.value=false;}}
defineExpose({show});
</script>
<template><el-dialog v-model="open" title="确认移入回收站" width="560px"><template v-if="info"><p>{{info.cohort}} · {{info.students}} 名学生 · {{info.photos}} 份照片文件 · {{info.deliveries}} 个交付关联 · {{info.jobs}} 个任务</p><el-alert v-if="info.expanded" title="选中学生共享交付包或任务，必须将下列完整关联范围一起移入；不会拆散其他学生的交付包。" type="warning" :closable="false"/><p class="muted">{{info.student_ids.join('、')}}</p><p>原图、成片、版本、参数、审核、交付和任务记录将一起保留；回收后不计入正常统计。</p><el-input v-if="whole" v-model="confirmation" :placeholder="'输入 '+info.cohort+' 确认清空'" aria-label="清空届次确认"/></template><template #footer><el-button @click="open=false">取消</el-button><el-button type="danger" @click="move" :loading="busy" :disabled="whole&&confirmation!==info?.cohort">确认移入完整范围</el-button></template></el-dialog></template>
