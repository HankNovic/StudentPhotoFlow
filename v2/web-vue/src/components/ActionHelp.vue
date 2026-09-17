<script setup>
defineProps({text:String,label:String});
</script>
<template><el-tooltip :content="text" placement="top" :show-after="150"><button type="button" class="action-help" :aria-label="label+'说明'" @click.stop>?</button></el-tooltip></template>
<style scoped>
.action-help{border:1px solid #b9b7b1;border-radius:50%;width:18px;height:18px;padding:0;background:transparent;color:#787774;font-size:12px;line-height:16px;cursor:help;flex-shrink:0}.action-help:focus-visible{outline:2px solid #2383e2;outline-offset:2px}
</style>
