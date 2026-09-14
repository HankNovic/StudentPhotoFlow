import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
export default defineConfig({
  plugins:[vue()],
  publicDir:false,
  build:{outDir:process.env.SPF_VUE_OUT||'../../.portable-build/vue-web',emptyOutDir:true}
});
