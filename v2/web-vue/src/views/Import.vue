<script setup>
import { ref,reactive,watch,computed } from 'vue';
import {useDraft,confirmLeave} from '../drafts';
import { ElMessage,ElMessageBox } from 'element-plus';
import { api,ids,run,url } from '../api';
import { state,refresh,changeCohort,loadCohorts } from '../workspace';
const emit=defineEmits(['navigate']);
const roster=ref(''),excel=ref(null),zip=ref(null),zipCheck=ref(null),photos=ref([]),report=ref(null),zipReport=ref(null),migration=ref(null),legacy=ref(''),historyIds=ref(''),historyReason=ref(''),confirmed=ref(false),busy=ref(false);
const fields=reactive({sheet:'',header_row:1,id_column:'A',image_column:'B',name_column:''});
const cohortDialog=ref(false),chosen=ref(''),checked=ref(false),detectColumns=ref(true);
watch(()=>[excel.value,fields.sheet,fields.header_row],()=>{checked.value=false;report.value=null;detectColumns.value=true;},{flush:'sync'});
watch(()=>[fields.id_column,fields.image_column,fields.name_column],()=>{checked.value=false;detectColumns.value=false;},{flush:'sync'});
async function operation(fn){busy.value=true;try{await fn();}finally{busy.value=false;}}
const rosterBase=ref(''),rosterCount=ref(null),rosterError=ref(''),rosterLoading=ref(false);
const dirty=computed(()=>roster.value!==rosterBase.value||!!historyIds.value||!!historyReason.value||!!excel.value||!!zip.value||photos.value.length>0);
function discard(){zipCheck.value=null;roster.value=rosterBase.value;historyIds.value='';historyReason.value='';excel.value=null;zip.value=null;photos.value=[];checked.value=false;}
useDraft('import',dirty,discard);
let rosterRequest=0;
async function loadRoster(){const cid=state.cohort,n=++rosterRequest;rosterLoading.value=true;try{const rows=await api('students',undefined,undefined,cid);if(cid!==state.cohort||n!==rosterRequest)return;const text=rows.map(s=>s.id).sort().join('\n');rosterCount.value=rows.length;rosterError.value='';if(roster.value===rosterBase.value){roster.value=text;rosterBase.value=text;}}catch(e){if(cid===state.cohort&&n===rosterRequest)rosterError.value='名单加载失败：'+e.message;}finally{if(n===rosterRequest)rosterLoading.value=false;}}
watch(()=>state.cohort,()=>{discard();rosterBase.value='';roster.value='';rosterCount.value=null;loadRoster();},{immediate:true});
watch(()=>state.students,()=>{if(!rosterLoading.value)loadRoster();});
async function addRoster(){const cid=state.cohort;await api('roster',{student_ids:ids(roster.value)},undefined,cid);if(cid!==state.cohort)return;rosterBase.value=roster.value;await loadRoster();await refresh();ElMessage.success('名单已校验并追加，重复学号已跳过');}
async function selectImportCohort(value){
 const target=state.allCohorts.find(c=>c.id===value||c.name===value);if(!target)throw Error('届次未登记，请先到系统设置添加');
 if(target.id!==state.cohort){const file=excel.value,inspection=report.value;if(!await confirmLeave())return false;await changeCohort(target.id);excel.value=file;report.value=inspection;}
 checked.value=report.value?.can_import===true;detectColumns.value=false;return checked.value;
}
async function inspect(){if(!excel.value)throw Error('请选择 Excel');await operation(async()=>{
 checked.value=false;
 const body=new FormData();body.append('file',excel.value);for(const [k,v] of Object.entries(fields))body.append(k,v);body.append('inspect_only',true);body.append('detect_columns',detectColumns.value);
 report.value=await api('imports/xlsx',body);fields.id_column=report.value.id_column;fields.image_column=report.value.image_column;fields.name_column=report.value.name_column||'';
 detectColumns.value=false;
 if(!report.value.can_import){ElMessage.error('表格检查未通过，请修正下方学号或姓名冲突');return;}
 if(report.value.cohort){if(!await selectImportCohort(report.value.cohort))return;ElMessage.success('表格检查完成，已识别 '+report.value.cohort);}
 else{chosen.value=state.cohort;cohortDialog.value=true;}
});}
async function choose(){if(!await selectImportCohort(chosen.value))return;cohortDialog.value=false;ElMessage.success('表格检查完成，已选择 '+chosen.value);}
async function importExcel(){if(!checked.value)throw Error('请先检查表格并确定届次');await operation(async()=>{
 const body=new FormData();body.append('file',excel.value);for(const [k,v] of Object.entries(fields))body.append(k,v);body.append('cohort',state.cohortName);body.append('inspect_only',false);
 await api('imports/xlsx',body);excel.value=null;await refresh();emit('navigate','jobs');ElMessage.success('Excel 接收任务已创建');
});}
async function zipAction(inspectOnly){if(!zip.value)throw Error('请选择照片压缩包');await operation(async()=>{
 const body=new FormData();body.append('file',zip.value);body.append('cohort',state.cohortName);body.append('inspect_only',inspectOnly);
 if(!inspectOnly){if(!zipCheck.value||zipCheck.value.name!==zip.value.name||zipCheck.value.cohort!==state.cohortName)throw Error('请先为当前压缩包和届次重新检查');await ElMessageBox.confirm('检查已通过，确认接收压缩包原图？','确认接收',{confirmButtonText:'确认接收',cancelButtonText:'取消'});body.append('check_token',zipCheck.value.token);}
 zipReport.value=await api('imports/zip',body);if(inspectOnly){zipCheck.value={name:zip.value.name,cohort:state.cohortName,token:zipReport.value.check_token};}else{zip.value=null;zipCheck.value=null;await refresh();emit('navigate','jobs');}ElMessage.success(inspectOnly?'压缩包检查完成':'压缩包接收任务已创建');
});}
async function uploadPhotos(){if(!photos.value.length)throw Error('请选择照片');await operation(async()=>{
 const cid=state.cohort;for(const file of photos.value){const body=new FormData();body.append('photo',file.raw);await api('students/'+encodeURIComponent(file.name.replace(/\.[^.]+$/,''))+'/photos',body,undefined,cid);}
 photos.value=[];await refresh();ElMessage.success('照片上传完成');
});}
async function historical(){if(!ids(historyIds.value).length)throw Error('请先粘贴至少一个已交付学号');await api('historical-deliveries',{student_ids:ids(historyIds.value),reason:historyReason.value,confirmed_sent:confirmed.value});historyIds.value='';historyReason.value='';await refresh();ElMessage.success('历史交付名单已登记');}
async function migrate(apply){migration.value=await api('migrations/legacy',{path:legacy.value,apply});if(apply){await loadCohorts();await refresh();}ElMessage.success(apply?'旧数据已复制迁移':'旧数据检查完成');}
function photoList(file,list){photos.value=list;}
</script>
<template>
 <el-card shadow="never"><template #header><h3>导入学号名单 <span data-testid="roster-count">{{rosterCount===null?'人数加载中':'共 '+rosterCount+' 人'}}</span></h3></template><p class="muted">一行一个学号，追加到当前 {{state.cohortName}}；保留前导零。姓名需要通过 Excel 导入补充。仅校验并追加；从输入框删行不会删除已保存学生，删除请移入回收站。</p><el-alert v-if="rosterError" :title="rosterError" type="error" :closable="false"/><p v-if="roster!==rosterBase" class="muted">名单有未提交输入，后台刷新不会覆盖。</p><el-input v-model="roster" type="textarea" :rows="5" aria-label="学号名单"/><div class="toolbar"><el-button type="primary" @click="addRoster">校验并添加名单</el-button><el-upload :auto-upload="false" :show-file-list="false" accept=".txt" :on-change="file=>run(async()=>{roster=await file.raw.text();})"><el-button>读取学号 TXT</el-button></el-upload><el-button tag="a" :href="url('exports/students.txt')">导出当前届全部学号 TXT</el-button><el-button tag="a" :href="url('exports/delivered.txt')">导出当前届已交付 TXT</el-button></div></el-card>
 <el-card shadow="never" data-testid="excel-import"><template #header><h3>导入 Excel 照片</h3></template><p class="muted">首次检查自动识别列；调整学号、图片或姓名列后请重新检查。姓名可选：非空更新，空值不覆盖已有姓名；同批同学号姓名冲突拒绝整批接收。多届次表格会拒绝导入。</p>
  <el-upload :auto-upload="false" :show-file-list="false" accept=".xlsx" :disabled="busy" :on-change="file=>{excel=file.raw;}"><el-button>选择 Excel</el-button></el-upload><p>{{excel?.name}}</p>
  <el-form label-position="top" class="config-grid" :disabled="busy"><el-form-item label="工作表"><el-input v-model="fields.sheet" placeholder="留空使用第一张"/></el-form-item><el-form-item label="表头行"><el-input-number v-model="fields.header_row" :min="1"/></el-form-item><el-form-item label="学号列"><el-input v-model="fields.id_column" aria-label="学号列"/></el-form-item><el-form-item label="图片列"><el-input v-model="fields.image_column" aria-label="图片列"/></el-form-item><el-form-item label="姓名列（可选）"><el-input v-model="fields.name_column" aria-label="姓名列" placeholder="留空表示没有姓名"/></el-form-item></el-form>
  <div class="toolbar"><el-button @click="inspect" :loading="busy">检查表格</el-button><el-button type="primary" @click="importExcel" :disabled="!checked" :loading="busy">接收原始图片</el-button></div><template v-if="report">
   <p data-testid="name-column-result">姓名列：{{report.name_column ? report.name_column+' · '+report.name_header : '未选择（兼容仅学号和照片的表格）'}}</p>
   <el-alert v-if="report.summary.name_empty" type="warning" :closable="false" :title="'第 '+report.summary.name_empty_rows.join('、')+' 行姓名为空，不覆盖已有姓名；使用姓名模板前需补齐。'"/>
   <el-alert v-for="c in report.summary.name_conflicts" :key="c.student_id" type="error" :closable="false" :title="'姓名冲突：'+c.student_id+'，第 '+c.rows.join('、')+' 行：'+c.names.join(' / ')"/>
   <el-alert v-if="report.summary.duplicate_count||report.summary.empty_ids" type="error" :closable="false" :title="'空学号 '+report.summary.empty_ids+' 行；重复学号：'+report.summary.duplicate_ids.join('、')"/>
   <p v-if="!checked" class="muted">请完成检查并确认届次后再接收。更改列选择后需重新检查。</p>
  </template><pre v-if="report" data-testid="excel-report">{{JSON.stringify(report,null,2)}}</pre>
 </el-card>
 <el-card shadow="never" data-testid="zip-import"><template #header><h3>导入照片压缩包</h3></template><p class="muted">压缩包名称不限，图片文件名为“学号-姓名.png”等。仅按学号识别，不从文件名推断或保存姓名。重复照片不增加版本，已交付记录跳过。</p><el-upload :auto-upload="false" :show-file-list="false" accept=".zip" :on-change="file=>{zip=file.raw;zipCheck=null;zipReport=null;}"><el-button>选择照片 ZIP</el-button></el-upload><p>{{zip?.name}}</p><div class="toolbar"><el-button @click="zipAction(true)" :loading="busy">检查压缩包</el-button><el-button type="primary" :disabled="!zipCheck" @click="zipAction(false)" :loading="busy">接收压缩包原图</el-button></div><pre v-if="zipReport" data-testid="zip-report">{{JSON.stringify(zipReport,null,2)}}</pre></el-card>
 <el-card shadow="never" data-testid="photo-upload"><template #header><h3>批量上传图片</h3></template><p class="muted">文件名为学号；学号须已在名单中。</p><el-upload :auto-upload="false" multiple accept="image/*" :on-change="photoList" :on-remove="photoList"><el-button>选择原图</el-button></el-upload><el-button @click="uploadPhotos" :loading="busy">上传所选原图</el-button></el-card>
 <el-card shadow="never" data-testid="historical"><template #header><h3>登记历史已交付名单</h3></template><p class="muted">未登记学号会自动加入当前届名单。只登记交付事实，不将当前成片标为已审核。</p><el-input v-model="historyIds" type="textarea" :rows="4" aria-label="历史已交付学号"/><el-input v-model="historyReason" placeholder="交付依据，例如已发送办卡第一批" class="spaced"/><el-checkbox v-model="confirmed">确认这些照片已经实际发送</el-checkbox><div><el-button @click="historical">登记已交付名单</el-button></div></el-card>
 <el-alert title="支持 rc.4 工作区保留数据升级；rc.3 及更早目录仍不支持直接导入。" type="info" :closable="false"/>
 <el-dialog v-model="cohortDialog" title="选择学生届次" width="440px" :close-on-click-modal="false"><p>未识别到学年，请选择本次导入所属届次；未登记届次请先到系统设置添加。</p><el-select v-model="chosen" filterable aria-label="导入届次"><el-option v-for="c in state.cohorts" :key="c.id" :value="c.id" :label="c.name"/></el-select><template #footer><el-button @click="cohortDialog=false">取消</el-button><el-button type="primary" @click="choose">确定届次</el-button></template></el-dialog>
</template>
