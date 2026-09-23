<script setup>
import {ref,computed,watch} from 'vue';
const props=defineProps({items:{type:Array,default:()=>[]}});
// Reuse Students' processing-plan defaults; view state only, not a saved setting.
const page=ref(1),size=ref(20),sizes=[20,50,100];
const total=computed(()=>props.items.length);
const start=computed(()=>total.value?(page.value-1)*size.value+1:0);
const end=computed(()=>Math.min(page.value*size.value,total.value));
const shown=computed(()=>props.items.slice((page.value-1)*size.value,page.value*size.value));
watch(()=>props.items,()=>{page.value=1;},{flush:'sync'});
watch(size,()=>{page.value=1;},{flush:'sync'});
watch(total,()=>{page.value=Math.max(1,Math.min(page.value,Math.ceil(total.value/size.value)));},{flush:'sync'});
</script>
<template>
 <div class="paged-table">
  <p class="muted" role="status">共 {{total}} 条，当前显示第 {{start}}–{{end}} 条</p>
  <el-pagination v-model:current-page="page" v-model:page-size="size" :page-sizes="sizes" layout="total,sizes,prev,pager,next" :total="total"/>
  <el-table :data="shown" max-height="40vh"><slot/></el-table>
 </div>
</template>
<style scoped>
.paged-table{min-height:0}.paged-table p{margin:8px 0}.el-pagination{margin:8px 0;flex-wrap:wrap;gap:4px}
</style>
