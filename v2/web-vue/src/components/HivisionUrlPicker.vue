<script setup>
import { state } from '../workspace';
import { api } from '../api';
import { ElMessage } from 'element-plus';
defineProps({modelValue:String,readonlyHistory:Boolean});
const emit=defineEmits(['update:modelValue']);
async function remove(url){state.urls=await api('engines/hivision/urls?url='+encodeURIComponent(url),undefined,'DELETE');ElMessage.success('历史地址已删除，当前配置保持原值');}
</script>
<template>
  <el-select :model-value="modelValue" @update:model-value="emit('update:modelValue',$event)" filterable allow-create default-first-option placeholder="输入地址或选择历史地址" aria-label="Hivision API地址">
    <el-option v-for="url in state.urls" :key="url" :label="url" :value="url">
      <span class="url-option">{{url}}<el-button v-if="!readonlyHistory" link type="danger" :aria-label="'删除历史地址 '+url" @click.stop="remove(url)">×</el-button></span>
    </el-option>
  </el-select>
</template>
