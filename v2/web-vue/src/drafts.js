import {computed,reactive,onBeforeUnmount,watch} from 'vue';
import {ElMessageBox} from 'element-plus';
const entries=reactive({});
export const hasDrafts=computed(()=>Object.values(entries).some(x=>x.dirty));
export function useDraft(key,dirty,discard){watch(dirty,value=>{entries[key]={dirty:value,discard};},{immediate:true,flush:'sync'});onBeforeUnmount(()=>delete entries[key]);}
export async function confirmLeave(){if(!hasDrafts.value)return true;try{await ElMessageBox.confirm('有未提交输入。可取消并回到页面保存，或放弃输入后继续。','未保存内容',{confirmButtonText:'放弃输入并继续',cancelButtonText:'返回保存',type:'warning'});for(const e of Object.values(entries))if(e.dirty)await e.discard();return true;}catch(e){if(e==='cancel'||e==='close')return false;throw e;}}
export function protectUnload(event){if(hasDrafts.value){event.preventDefault();event.returnValue='';}}
