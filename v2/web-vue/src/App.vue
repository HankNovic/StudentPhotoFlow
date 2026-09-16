<script setup>
import { ref,onMounted,onUnmounted } from 'vue';
import { ElMessage,ElMessageBox } from 'element-plus';
import { api,report,run } from './api';
import {confirmLeave,protectUnload} from './drafts';
import { state,initialize,refresh,changeCohort } from './workspace';
import Students from './views/Students.vue';
import Import from './views/Import.vue';
import Config from './views/Config.vue';
import Jobs from './views/Jobs.vue';
import Deliveries from './views/Deliveries.vue';
import Settings from './views/Settings.vue';
const pages={students:'学生与审核',import:'数据接入',config:'处理配置',jobs:'任务进度',deliveries:'照片交付',settings:'系统设置'};
const remembered=sessionStorage.getItem('spf-page'),page=ref(pages[remembered]?remembered:'students'),failure=ref(''),checking=ref(false),needsLogin=ref(false),loginToken=ref('');
const deployed=ref(null),pageBuild=__SPF_BUILD_ID__;
let timer,versionTimer;
async function checkDeployment(){try{const r=await fetch('/healthz',{cache:'no-store'});if(r.ok){const d=await r.json();deployed.value=d.build_id&&d.build_id!==pageBuild?d:null;}}catch{}}
async function reloadPage(){if(await confirmLeave())location.reload();}
async function navigate(value){if(value!==page.value&&!await confirmLeave())return;page.value=value;sessionStorage.setItem('spf-page',value);}
async function switchCohort(value){if(value===state.cohort||!await confirmLeave())return;await changeCohort(value);ElMessage.success('已切换到 '+state.cohortName);}
async function update(){
 checking.value=true;try{const d=await api('update');
 if(d.ok===false){ElMessage.warning(d.message);return;} const newer=d.version&&d.version!==state.health.version;
 if(newer){await ElMessageBox.confirm('发现版本 '+d.version+'，打开发布页下载？','检查更新',{confirmButtonText:'打开发布页',cancelButtonText:'取消'});if(d.url?.startsWith('https://github.com/HankNovic/StudentPhotoFlow/'))window.open(d.url,'_blank','noopener');}
 else ElMessage.success('当前已是最新版本');}finally{checking.value=false;}
}
async function startWorkspace(){await initialize();needsLogin.value=false;failure.value='';if(!state.cohort)page.value='settings';clearInterval(timer);timer=setInterval(async()=>{await checkDeployment();if(state.ready&&!deployed.value&&!needsLogin.value)run(refresh);},2500);}
async function login(){if(state.ready&&!await confirmLeave())return;const r=await fetch('/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:loginToken.value})});if(!r.ok){failure.value='令牌错误';return;}loginToken.value='';await startWorkspace();}
function expired(){needsLogin.value=true;failure.value='会话已失效，请重新登录';clearInterval(timer);}
async function logout(){if(!await confirmLeave())return;await fetch('/logout',{method:'POST'});expired();failure.value='';}
onMounted(async()=>{window.addEventListener('beforeunload',protectUnload);checkDeployment();versionTimer=setInterval(checkDeployment,4000);window.addEventListener('spf-session-expired',expired);try{await startWorkspace();}catch(e){expired();failure.value='';}});
onUnmounted(()=>{clearInterval(versionTimer);window.removeEventListener('beforeunload',protectUnload);clearInterval(timer);window.removeEventListener('spf-session-expired',expired);});
</script>
<template>
 <div class="app" :data-build="pageBuild"><div v-if="deployed" class="deployment-notice" role="alert"><strong>服务已更新，请刷新页面</strong><span>当前服务 {{deployed.version}}</span><el-button @click="reloadPage">刷新页面</el-button></div>
  <div v-if="needsLogin" class="login-page"><div class="login-panel"><div class="brand">StudentPhotoFlow</div><p class="subtitle">学生照片工作台</p><h1>管理员登录</h1><p class="muted">请输入管理员访问令牌以继续</p><el-input v-model="loginToken" type="password" show-password placeholder="访问令牌" @keyup.enter="login"/><el-button type="primary" @click="login">登录</el-button><el-alert v-if="failure && failure!=='请先输入管理员令牌'" :title="failure" type="error" :closable="false"/></div></div>
  <div v-show="!needsLogin"><aside class="sidebar"><div class="brand">StudentPhotoFlow</div><p class="subtitle">学生照片工作台</p>
   <el-menu :default-active="page" @select="navigate"><el-menu-item v-for="(title,key) in pages" :key="key" :index="key">{{title}}</el-menu-item></el-menu>
   <div class="sidebar-bottom"><p class="sidebar-label">当前届次</p><el-select :model-value="state.cohort" @update:model-value="switchCohort" aria-label="当前届次" :disabled="!state.ready"><el-option v-if="state.archived" :value="state.cohort" :label="state.cohortName+'（归档只读）'"/><el-option v-for="c in state.cohorts" :key="c.id" :value="c.id" :label="c.name"/></el-select>
   <p class="workspace">{{state.health.workspace}}</p><p class="sidebar-label">版本 {{state.health.version||'—'}}</p>
   <el-button @click="update" :loading="checking" :disabled="!state.ready">检查更新</el-button>
   <el-button @click="navigate('settings')">届次及回收站管理</el-button><el-button @click="logout">退出登录</el-button></div>
  </aside>
  <main><header><div><p class="eyebrow">STUDENT PHOTO WORKSPACE</p><h1>{{pages[page]}}</h1><p class="muted" v-if="state.cohort">所属届次：{{state.cohortName}}</p></div><el-button @click="refresh" :disabled="!state.ready">刷新</el-button></header>
   <el-alert v-if="failure" :title="failure" type="error" :closable="false"/>
   <el-skeleton v-if="!state.ready&&!failure" :rows="6" animated/>
   <el-alert v-if="state.archived" title="当前届次已归档，只读查看；恢复后才能录入或处理。" type="warning" :closable="false"/><el-alert v-if="state.ready&&!state.cohort" title="暂无可用届次，请到系统设置添加并保存。" type="info" :closable="false"/><template v-if="state.ready"><section v-show="page==='students'"><Students @navigate="navigate"/></section><section v-if="page==='import'&&state.cohort"><Import @navigate="navigate"/></section><section v-show="page==='config'"><Config/></section><section v-show="page==='jobs'"><Jobs/></section><section v-show="page==='deliveries'"><Deliveries/></section><section v-if="page==='settings'"><Settings/></section></template>
  </main></div>
 </div>
</template>

