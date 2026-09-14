<script setup>
import { art } from '../api';
defineProps({artifact:Object,label:{type:String,default:'照片'}});
</script>
<template><figure class="photo"><el-image v-if="artifact" :src="art(artifact)" :alt="label" :preview-src-list="[art(artifact)]" preview-teleported fit="contain"/><el-empty v-else description="暂无照片" :image-size="40"/><figcaption>{{label}} <span v-if="artifact">{{artifact.width}}×{{artifact.height}}</span></figcaption></figure></template>