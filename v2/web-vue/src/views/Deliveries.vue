<script setup>
import { state,refresh } from '../workspace';
import { api } from '../api';
import { ElMessage,ElMessageBox } from 'element-plus';
const labels={prepared:'待实际交付',delivered:'已交付',cancelled:'已取消'};
async function action(batch,value){
 if(value==='confirm')await ElMessageBox.confirm('确认照片已实际发送给接收方？','确认实际交付',{confirmButtonText:'确认已发送',cancelButtonText:'取消'});
 await api('deliveries/'+batch.id+'/'+value,{});await refresh();ElMessage.success(value==='confirm'?'已确认交付':'交付包已取消');
}
</script>
<template>
 <p class="muted">显示整个工作区交付记录。生成交付包后，实际发送完成再确认已交付。</p><el-empty v-if="!state.deliveries.length" description="暂无交付批次"/>
 <el-card v-for="batch in state.deliveries" :key="batch.id" shadow="never" data-testid="delivery">
  <div class="section-heading"><h3>{{batch.historical?'历史登记':'照片交付包'}} · {{batch.id.slice(0,16)}}</h3><el-tag>{{labels[batch.status]}}</el-tag></div>
  <p>{{batch.items.length}} 人 · {{batch.at}}</p>
  <div class="toolbar"><el-button v-if="!batch.historical&&batch.status!=='cancelled'" tag="a" :href="'/api/v1/deliveries/'+batch.id+'/download'">下载学号命名照片包</el-button><el-button v-if="batch.status==='prepared'" type="primary" @click="action(batch,'confirm')">确认这批照片已经实际交付</el-button><el-button v-if="batch.status==='prepared'" @click="action(batch,'cancel')">取消交付包</el-button></div>
  <el-collapse><el-collapse-item title="查看学号"><p>{{batch.items.map(x=>x.student_id).join('、')}}</p></el-collapse-item></el-collapse>
 </el-card>
</template>