<script setup>
import { ref,onMounted,onUnmounted } from 'vue';
import { ElMessage,ElMessageBox } from 'element-plus';
import { api,report,run } from './api';
import { state,initialize,refresh,changeCohort } from './workspace';
import Students from './views/Students.vue';
import Import from './views/Import.vue';
import Config from './views/Config.vue';
import Jobs from './views/Jobs.vue';
import Deliveries from './views/Deliveries.vue';
const pages={students:'学生与审核',import:'数据接入',config:'处理配置',jobs:'任务进度',deliveries:'照片交付'};
const remembered=sessionStorage.getItem('spf-page'),page=ref(pages[remembered]?remembered:'students'),failure=ref(''),checking=ref(false);
let events,timer;
function navigate(value){page.value=value;sessionStorage.setItem('spf-page',value);}
async function switchCohort(value){await changeCohort(value);ElMessage.success('已切换到 '+value);}
async function update(){
 checking.value=true;try{const d=await api('update');
 const version=v=>v.split('.').map(Number),a=version(d.version||'0'),b=version(state.health.version),newer=a.some((x,i)=>x>(b[i]||0)&&a.slice(0,i).every((y,k)=>y===(b[k]||0)));
 if(newer){await ElMessageBox.confirm('发现版本 '+d.version+'，打开发布页下载？','检查更新',{confirmButtonText:'打开发布页',cancelButtonText:'取消'});if(d.url?.startsWith('https://github.com/HankNovic/StudentPhotoFlow/'))window.open(d.url,'_blank','noopener');}
 else ElMessage.success('当前已是最新版本');}finally{checking.value=false;}
}
async function exit(){await api('shutdown',{});events?.close();ElMessage.success('程序已退出，可以关闭网页');}
onMounted(async()=>{try{await initialize();events=new EventSource('/api/v1/events');events.onmessage=()=>{clearTimeout(timer);timer=setTimeout(()=>run(refresh),200);};}catch(e){failure.value=e.message;report(e);}});
onUnmounted(()=>{events?.close();clearTimeout(timer);});
</script>
<template>
 <div class="app">
  <aside class="sidebar"><div class="brand">StudentPhotoFlow</div><p class="subtitle">学生照片工作台</p>
   <el-menu :default-active="page" @select="navigate"><el-menu-item v-for="(title,key) in pages" :key="key" :index="key">{{title}}</el-menu-item></el-menu>
   <div class="sidebar-bottom"><p class="sidebar-label">当前届次</p><el-select :model-value="state.cohort" @update:model-value="switchCohort" aria-label="当前届次" :disabled="!state.ready"><el-option v-for="c in state.cohorts" :key="c" :value="c"/></el-select>
   <p class="workspace">{{state.health.workspace}}</p><p class="sidebar-label">版本 {{state.health.version||'—'}}</p>
   <el-button @click="update" :loading="checking" :disabled="!state.ready">检查更新</el-button>
   <el-link href="/docs" target="_blank">API 接口文档 ↗</el-link><el-button @click="exit" :disabled="!state.ready">退出程序</el-button></div>
  </aside>
  <main><header><div><p class="eyebrow">STUDENT PHOTO WORKSPACE</p><h1>{{pages[page]}}</h1></div><el-button @click="refresh" :disabled="!state.ready">刷新</el-button></header>
   <el-alert v-if="failure" :title="failure" type="error" :closable="false"/>
   <el-skeleton v-if="!state.ready&&!failure" :rows="6" animated/>
   <template v-if="state.ready"><section v-show="page==='students'"><Students @navigate="navigate"/></section><section v-show="page==='import'"><Import @navigate="navigate"/></section><section v-show="page==='config'"><Config/></section><section v-show="page==='jobs'"><Jobs/></section><section v-show="page==='deliveries'"><Deliveries/></section></template>
  </main>
 </div>
</template>