import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import {randomUUID} from 'node:crypto';
const buildId=process.env.SPF_BUILD_ID||randomUUID();
export default defineConfig({
  plugins:[vue(),{name:'build-identity',generateBundle(){this.emitFile({type:'asset',fileName:'build-info.json',source:JSON.stringify({build_id:buildId})});}}],
  define:{__SPF_BUILD_ID__:JSON.stringify(buildId)},
  publicDir:false,
  build:{outDir:process.env.SPF_VUE_OUT||'../../.portable-build/vue-web',emptyOutDir:true}
});
