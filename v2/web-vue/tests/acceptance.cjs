const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn,spawnSync}=require('node:child_process');
const {chromium}=require('playwright');
const [,,executable,output]=process.argv;
if(!executable||!output)throw Error('Usage: node acceptance.cjs EXE OUTPUT');
fs.mkdirSync(output,{recursive:true});
const evidence={executable:path.resolve(executable),checks:[],screenshots:[],errors:[]};
let child,browser,page,base,mock,lastJob,slow=false;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function poll(fn,timeout=20000){const end=Date.now()+timeout;while(Date.now()<end){const x=await fn();if(x)return x;await sleep(200);}throw Error('Timed out waiting for state');}
function ok(name){evidence.checks.push({name,status:'passed',at:new Date().toISOString()});console.log('PASS',name);}
async function screenshot(name){await poll(async()=>await page.locator('.el-message').count()===0,10000);await page.waitForTimeout(250);await page.evaluate(()=>window.scrollTo(0,0));const file=path.join(output,name+'.png');await page.screenshot({path:file,fullPage:true});evidence.screenshots.push(file);}
async function click(text){await page.getByRole('button',{name:text,exact:true}).click();}
async function nav(text){await page.getByRole('menuitem',{name:text,exact:true}).click();}
async function data(route){const r=await page.request.get(base+'/api/v1/'+route);assert.equal(r.status(),200);return r.json();}
async function waitJob(){await poll(async()=>{const jobs=await data('jobs');if(jobs.length&&jobs[0].id!==lastJob&&jobs[0].status==='completed'){lastJob=jobs[0].id;return true;}return false;},45000);await click('刷新');}
async function saveDownload(action,name){const event=page.waitForEvent('download');await action();const download=await event;await download.saveAs(path.join(output,name));return path.join(output,name);}
(async()=>{
 const workspace=path.join(output,'workspace');fs.mkdirSync(workspace,{recursive:true});
 child=spawn(executable,['--workspace',workspace,'--no-browser'],{windowsHide:true,stdio:'ignore'});
 evidence.pid=child.pid;
 const runtimePath=path.join(workspace,'.v2-runtime.json');
 await poll(()=>fs.existsSync(runtimePath));
 const runtime=JSON.parse(fs.readFileSync(runtimePath));base='http://127.0.0.1:'+runtime.port;evidence.base=base;
 await poll(async()=>{try{return (await fetch(base)).ok;}catch{return false;}});
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
 page=await context.newPage();
 page.on('pageerror',e=>evidence.errors.push(e.message));
 assert.equal((await page.request.get(base+'/api/v1/health')).status(),403);
 ok('Unauthenticated API rejected');
 const session=page.waitForResponse(r=>r.url()===base+'/session'&&r.request().method()==='POST');
 await page.goto(base+'/#token='+runtime.token);assert.equal((await session).status(),200);
 await page.getByRole('button',{name:'刷新',exact:true}).waitFor();
 await poll(()=>page.getByRole('button',{name:'刷新',exact:true}).isEnabled());
 assert.equal(await page.title(),'StudentPhotoFlow');
 assert.equal(new URL(page.url()).hash,'');
 const cookies=await context.cookies();assert(cookies.some(c=>c.name==='spf_session'&&c.httpOnly));
 assert.equal(await page.locator('iframe').count(),0);
 const health=await data('health');evidence.health=health;
 const bundled=JSON.parse(fs.readFileSync(path.join(path.dirname(executable),'build-info.json'),'utf8'));
 assert.equal(health.source_commit,bundled.source_commit);assert.equal(health.build_id,bundled.build_id);
 assert.equal(path.resolve(health.workspace),path.resolve(workspace));
 const duplicate=spawn(executable,['--workspace',workspace,'--no-browser'],{windowsHide:true,stdio:'ignore'});await new Promise((resolve,reject)=>{duplicate.on('exit',code=>code===0?resolve():reject(Error('Duplicate launch failed')));duplicate.on('error',reject);});assert.deepEqual(JSON.parse(fs.readFileSync(runtimePath)),runtime);ok('Second EXE invocation reuses workspace instance without opening user browser');
 await screenshot('01-students-auth');ok('EXE token → session → HttpOnly Cookie; exact candidate identity; single Vue navigation');
 const fixtures=path.join(output,'fixtures');
 const py=path.resolve(__dirname,'../../..','.portable-build/venv/Scripts/python.exe');
 const seed=path.resolve(__dirname,'../../../tests/make_acceptance_data.py');
 mock=require('node:http').createServer(async(req,res)=>{
  if(req.url==='/openapi.json'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({info:{title:'Synthetic Hivision contract'},paths:{'/idphoto':{post:{requestBody:{content:{'multipart/form-data':{schema:{properties:Object.fromEntries(['input_image','height','width','human_matting_model','face_detect_model'].map(x=>[x,{}]))}}}}}}}}));}
  else if(req.url.startsWith('/photo/')){if(slow)await sleep(1800);res.setHeader('Content-Type','image/png');res.end(fs.readFileSync(path.join(fixtures,'00003.png')));}
  else{res.statusCode=404;res.end();}
 });
 await new Promise(r=>mock.listen(0,'127.0.0.1',r));const mockUrl='http://127.0.0.1:'+mock.address().port;
 const seeded=spawnSync(py,[seed,fixtures,mockUrl],{encoding:'utf8'});assert.equal(seeded.status,0,seeded.stderr);
 const docsEvent=page.waitForEvent('popup');await page.getByRole('link',{name:'API 接口文档 ↗'}).click();const docs=await docsEvent;await docs.waitForLoadState('domcontentloaded');assert(new URL(docs.url()).pathname==='/docs');assert((await docs.title()).includes('Swagger'));await docs.close();ok('API documentation opens from candidate page');
 const updateResponse=page.waitForResponse(r=>r.url()===base+'/api/v1/update');await click('检查更新');const update=await updateResponse;evidence.update={status:update.status(),body:await update.json()};
 if(update.ok()){const message=page.getByRole('dialog',{name:'检查更新'});if(await message.isVisible())await message.getByRole('button',{name:'取消',exact:true}).click();ok('Online update check returned GitHub release metadata');}
 else{await page.getByText(/检查更新失败/).waitFor();ok('Update network failure displayed (remote check not passed)');}
 await nav('数据接入');
 await page.getByLabel('学号名单',{exact:true}).fill('TEMP2026');
 await click('校验并添加名单');
 await poll(async()=>(await data('students')).some(x=>x.id==='TEMP2026'));
 const cohort=page.getByRole('combobox',{name:'当前届次',exact:true});
 await cohort.press('ArrowDown');await page.getByRole('option',{name:'2025级',exact:true}).click();
 await poll(async()=>(await data('students')).length===0);
 await cohort.press('ArrowDown');await page.getByRole('option',{name:'2026级',exact:true}).click();
 await poll(async()=>(await data('students')).length===1);ok('Cohort switching refreshes isolated student list');
 await page.locator('[data-testid="zip-import"] input[type=file]').setInputFiles(path.join(fixtures,'photos.zip'));
 await click('检查压缩包');await page.getByTestId('zip-report').getByText(/00001/).waitFor();
 await click('接收压缩包原图');await waitJob();
 assert.equal((await data('students')).filter(x=>x.status==='pending').length,2);ok('ZIP inspect and import via UI; original photos retained');
 await nav('处理配置');assert.deepEqual((await page.locator('[data-config]:visible').evaluateAll(nodes=>nodes.map(n=>n.dataset.config))).sort(),Object.keys((await data('config')).defaults).sort());
 await page.locator('[data-config="crop_enabled"]:visible').locator('.el-switch').click();
 await click('保存配置');await poll(async()=>(await data('config')).saved.crop_enabled===true);
 const exported=await saveDownload(()=>click('导出配置 JSON'),'config.json');const config=JSON.parse(fs.readFileSync(exported));assert.equal(config.crop_enabled,true);
 await page.getByRole('button',{name:'导入配置 JSON',exact:true}).locator('..').locator('input[type=file]').setInputFiles(exported);
 await page.getByText('配置已导入并保存',{exact:true}).waitFor();
 const address=page.getByRole('combobox',{name:'Hivision API地址',exact:true});
 await address.fill(mockUrl);await page.getByRole('option',{name:mockUrl,exact:true}).click();await click('添加当前地址到历史');
 await poll(async()=>(await data('engines/hivision/urls')).includes(mockUrl));
 await click('测试当前 API（不上传照片）');await page.getByTestId('engine-test-result').getByText(/Synthetic Hivision contract/).waitFor();
 await address.fill(mockUrl+'/temporary');await page.getByRole('option',{name:mockUrl+'/temporary',exact:true}).click();await click('添加当前地址到历史');
 await address.press('ArrowDown');await page.getByRole('option').filter({has:page.getByRole('button',{name:'删除历史地址 '+mockUrl,exact:true})}).click();
 await address.press('ArrowDown');await page.getByRole('button',{name:'删除历史地址 '+mockUrl+'/temporary',exact:true}).click();
 await page.keyboard.press('Escape');assert(!(await data('engines/hivision/urls')).includes(mockUrl+'/temporary'));
 ok('Hivision history add/switch/delete and local mock OpenAPI compatibility test (not real processing)');
 const invalid=page.waitForResponse(r=>r.url()===base+'/api/v1/config'&&r.request().method()==='PUT');await page.getByRole('button',{name:'导入配置 JSON',exact:true}).locator('..').locator('input[type=file]').setInputFiles({name:'invalid.json',mimeType:'application/json',buffer:Buffer.from('{"crop_enabled":"false"}')});assert.equal((await invalid).status(),409);await page.locator('.el-message--error').waitFor();assert.equal((await data('config')).saved.crop_enabled,true);ok('Invalid config error shown; saved config preserved');
 await screenshot('02-config');ok('All configuration controls; save, JSON export and import');
 await nav('学生与审核');assert.equal(await page.locator('main > section:visible').count(),1);
 await page.getByLabel('选择学生 00001',{exact:true}).locator('xpath=ancestor-or-self::label[1]').click();await page.getByLabel('选择学生 00002',{exact:true}).locator('xpath=ancestor-or-self::label[1]').click();
 await click('预览处理计划');const plan=page.getByRole('dialog',{name:'本次执行计划'});
 await plan.getByText('执行 2 人，跳过 0 人').waitFor();await screenshot('03-plan');await plan.getByRole('button',{name:'按此计划开始处理'}).click();await waitJob();
 const processed=(await data('students')).filter(x=>['00001','00002'].includes(x.id));
 assert(processed.every(s=>s.status==='review'&&s.result.artifact.width===295&&s.result.artifact.height===413));
 await screenshot('04-jobs');ok('Plan uses current config; start and real image processing');
 await nav('学生与审核');
 const row=page.getByTestId('student-table').getByRole('row').filter({hasText:'00001'});
 await row.getByRole('button',{name:'查看流程 / 审核'}).click();
 const detail=page.getByRole('dialog',{name:'学生 00001 · 流程与审核'});
 await detail.getByText('正式处理阶段结果',{exact:true}).waitFor();
 const before=await data('students/00001');await detail.getByRole('button',{name:'用当前配置试处理'}).click();
 await detail.getByText('试处理阶段结果（非正式成片）',{exact:true}).waitFor();
 assert.equal((await data('students/00001')).revision,before.revision);
 await detail.getByText('调整处理参数，再次预览',{exact:true}).click();await detail.getByRole('button',{name:'保存这些参数供批量处理'}).click();await page.getByText('预览参数已保存供批量处理',{exact:true}).waitFor();await detail.getByText('调整处理参数，再次预览',{exact:true}).click();
 await detail.getByText('版本与操作记录',{exact:true}).click();await screenshot('05-review-preview');
 await detail.getByRole('button',{name:'退回当前成片',exact:true}).click();
 await page.getByRole('dialog',{name:'退回当前成片',exact:true}).getByRole('textbox').fill('Test rejection');await click('保存');
 await poll(async()=>(await data('students/00001')).status==='review_rejected');
 await detail.getByRole('button',{name:'审核通过当前成片'}).click();await poll(async()=>(await data('students/00001')).status==='approved');
 await detail.getByRole('button',{name:'撤回当前审核'}).click();await poll(async()=>(await data('students/00001')).status==='review');
 await detail.getByRole('button',{name:'跳过 / 下一张'}).click();await page.getByRole('dialog',{name:'学生 00002 · 流程与审核'}).waitFor();
 await page.locator('.el-overlay-dialog:visible').click({position:{x:5,y:5}});await page.getByRole('dialog').waitFor({state:'hidden'});ok('Source/result/stages/history, single preview without revision changes, single approve/undo, next and close');
 await page.getByRole('checkbox',{name:'全选筛选结果',exact:true}).locator('xpath=ancestor-or-self::label[1]').click();
 await page.getByRole('textbox',{name:'搜索学号',exact:true}).fill('0000');
 await page.getByTestId('selection-count').getByText('已选 0 人').waitFor();
 await page.getByRole('checkbox',{name:'全选筛选结果',exact:true}).locator('xpath=ancestor-or-self::label[1]').click();
 await click('批量审核通过');const batch=page.getByRole('dialog',{name:'批量审核通过',exact:true});
 await batch.getByRole('textbox').fill('Synthetic acceptance');await batch.getByRole('button',{name:'确认批量审核'}).click();
 await poll(async()=>(await data('students')).filter(x=>x.status==='approved').length===2);
 await page.getByRole('checkbox',{name:'全选筛选结果',exact:true}).locator('xpath=ancestor-or-self::label[1]').click();await click('生成选中照片交付包');
 await poll(async()=>(await data('deliveries')).length===1);
 await saveDownload(()=>page.getByRole('link',{name:'下载学号命名照片包',exact:true}).click(),'delivery.zip');
 await screenshot('06-delivery');
 await click('确认这批照片已经实际交付');await click('确认已发送');await poll(async()=>(await data('deliveries'))[0].status==='delivered');
 const verify=spawnSync(py,['-c',`from zipfile import ZipFile
from PIL import Image
import io,json,sys
with ZipFile(sys.argv[1]) as z:
 names=z.namelist()
 photos=[n for n in names if n.endswith(('.jpg','.png','.jpeg'))]
 assert sorted(n.split('.')[0] for n in photos)==['00001','00002'],names
 for n in photos: assert Image.open(io.BytesIO(z.read(n))).size==(295,413)
 assert 'manifest.json' in names
 print(json.dumps({'files':names,'photo_dimensions':[295,413]}))
`,path.join(output,'delivery.zip')],{encoding:'utf8'});assert.equal(verify.status,0,verify.stderr);evidence.delivery=JSON.parse(verify.stdout);
 ok('Selection reset, batch approve, CREATE delivery, download names and photos, confirmed actual test delivery');
 await saveDownload(()=>page.getByRole('link',{name:'下载学号命名照片包',exact:true}).click(),'delivery-after-confirm.zip');
 assert.deepEqual(fs.readFileSync(path.join(output,'delivery.zip')),fs.readFileSync(path.join(output,'delivery-after-confirm.zip')));
 await nav('数据接入');
 await saveDownload(()=>page.getByRole('link',{name:'导出当前届已交付 TXT',exact:true}).click(),'delivered.txt');
 assert.deepEqual(fs.readFileSync(path.join(output,'delivered.txt'),'utf8').trim().split(/\r?\n/),['00001','00002']);
 await page.getByLabel('历史已交付学号',{exact:true}).fill('00009');
 await page.getByPlaceholder('交付依据，例如已发送办卡第一批').fill('Synthetic historical delivery');
 await page.getByRole('checkbox',{name:'确认这些照片已经实际发送'}).locator('xpath=ancestor-or-self::label[1]').click();await click('登记已交付名单');
 await poll(async()=>(await data('students')).some(s=>s.id==='00009'&&s.status==='delivered'));
 await screenshot('07-import-history');ok('Delivered TXT and historical delivery register');
 await page.locator('[data-testid="photo-upload"] input[type=file]').setInputFiles(path.join(fixtures,'00003.png'));
 await page.getByRole('button',{name:'读取学号 TXT',exact:true}).locator('..').locator('input[type=file]').setInputFiles(path.join(fixtures,'roster.txt'));await poll(async()=>await page.getByLabel('学号名单',{exact:true}).inputValue()==='00003\n');await click('校验并添加名单');
 await poll(async()=>(await data('students')).some(s=>s.id==='00003'));
 await click('上传所选原图');await poll(async()=>(await data('students/00003')).sources.length===1);ok('Roster and per-ID photo upload');
 await page.locator('[data-testid="excel-import"] input[type=file]').setInputFiles(path.join(fixtures,'single-year.xlsx'));
 await click('检查表格');await poll(()=>page.getByTestId('excel-report').textContent().then(x=>x.includes('2026级')));
 assert.equal(await page.getByLabel('学号列',{exact:true}).inputValue(),'B');assert.equal(await page.getByLabel('图片列',{exact:true}).inputValue(),'C');
 await click('接收原始图片');await waitJob();assert.equal((await data('students/00100')).sources.length,1);ok('Excel image URL import via actual page');
 await nav('数据接入');
 await page.locator('[data-testid="excel-import"] input[type=file]').setInputFiles(path.join(fixtures,'no-year.xlsx'));
 await click('检查表格');await page.getByRole('dialog',{name:'选择学生届次'}).waitFor();await click('确定届次');
 ok('Excel detects B/C columns and year; no year opens cohort chooser');
 await page.locator('[data-testid="excel-import"] input[type=file]').setInputFiles(path.join(fixtures,'multi-year.xlsx'));
 await click('检查表格');await page.getByText(/Excel中存在多个学年/).waitFor();ok('Excel multi-year rejection surfaced');
 await page.getByLabel('旧数据路径',{exact:true}).fill(path.join(fixtures,'legacy'));await click('检查旧数据');await page.getByTestId('migration-report').waitFor();
 await click('复制迁移到当前空工作区');await page.getByText('迁移目标必须是空V2工作区，避免混合数据',{exact:true}).waitFor();ok('Legacy inspect and nonempty-workspace protection');
 await page.locator('[data-testid="zip-import"] input[type=file]').setInputFiles(path.join(fixtures,'photos.zip'));await click('接收压缩包原图');await waitJob();
 const skipped=(await data('jobs'))[0];assert.equal(Object.keys(skipped.errors).length,2);await page.getByTestId('job').first().getByText('错误和计划',{exact:true}).click();await screenshot('08-jobs-skipped');ok('Delivered ZIP imports skipped and task errors displayed');
 await nav('数据接入');slow=true;await page.locator('[data-testid="excel-import"] input[type=file]').setInputFiles(path.join(fixtures,'slow.xlsx'));await click('检查表格');await click('接收原始图片');
 await page.getByTestId('job').first().getByRole('button',{name:'暂停',exact:true}).click();await poll(async()=>(await data('jobs'))[0].status==='paused');
 await page.getByTestId('job').first().getByRole('button',{name:'继续未完成项',exact:true}).click();await poll(async()=>(await data('jobs'))[0].status==='running');
 await page.getByTestId('job').first().getByRole('button',{name:'安全中断',exact:true}).click();await poll(async()=>(await data('jobs'))[0].status==='cancelled');
 await screenshot('09-jobs-controls');slow=false;await page.getByTestId('job').first().getByRole('button',{name:'继续未完成项',exact:true}).click();await waitJob();ok('Pause, resume, cancel and resume remaining Excel rows');
 await nav('数据接入');await saveDownload(()=>page.getByRole('link',{name:'导出当前届全部学号 TXT',exact:true}).click(),'all-students.txt');assert(fs.readFileSync(path.join(output,'all-students.txt'),'utf8').includes('00003'));
 await page.locator('[data-testid="photo-upload"] input[type=file]').setInputFiles({name:'00001.png',mimeType:'image/png',buffer:fs.readFileSync(path.join(fixtures,'00003.png'))});await click('上传所选原图');await poll(async()=>(await data('students/00001')).status==='delivered_updated');
 await nav('学生与审核');await page.getByRole('textbox',{name:'搜索学号',exact:true}).fill('00001');
 await page.getByTestId('student-table').getByRole('button',{name:'查看流程 / 审核'}).click();const replacement=page.getByRole('dialog',{name:'学生 00001 · 流程与审核'});
 await replacement.getByRole('button',{name:'启动替换流程'}).click();await page.getByRole('dialog',{name:'启动照片替换'}).getByRole('textbox').fill('Synthetic replacement test');await click('启动替换');await poll(async()=>(await data('students/00001')).replacement===true);await page.keyboard.press('Escape');
 await page.getByLabel('选择学生 00001',{exact:true}).locator('xpath=ancestor-or-self::label[1]').click();await click('预览处理计划');
 await page.getByRole('dialog',{name:'本次执行计划'}).getByRole('checkbox').locator('xpath=ancestor-or-self::label[1]').click();await click('重新计算计划');await click('按此计划开始处理');await waitJob();
 await nav('学生与审核');if(!await page.getByRole('checkbox',{name:'全选筛选结果'}).isChecked())await page.getByRole('checkbox',{name:'全选筛选结果'}).locator('xpath=ancestor-or-self::label[1]').click();await click('批量退回');await click('确认批量审核');await poll(async()=>(await data('students/00001')).status==='review_rejected');
 await page.getByTestId('student-table').getByRole('button',{name:'查看流程 / 审核'}).click();await page.getByRole('dialog',{name:'学生 00001 · 流程与审核'}).getByRole('button',{name:'审核通过当前成片'}).click();await poll(async()=>(await data('students/00001')).status==='approved');await page.keyboard.press('Escape');
 await page.getByRole('checkbox',{name:'全选筛选结果'}).locator('xpath=ancestor-or-self::label[1]').click();await saveDownload(()=>click('导出选中名单'),'selected.json');assert.deepEqual(JSON.parse(fs.readFileSync(path.join(output,'selected.json'))).student_ids,['00001']);await click('生成选中照片交付包');await click('取消交付包');await poll(async()=>(await data('deliveries')).some(x=>x.status==='cancelled'));ok('Replacement reason, new processing version, batch reject, selected JSON and delivery cancellation');
 // A second fresh EXE workspace verifies migration without modifying the source fixture.
 const legacyFiles=fs.readdirSync(path.join(fixtures,'legacy')).map(n=>[n,fs.readFileSync(path.join(fixtures,'legacy',n))]);
 await click('退出程序');await poll(async()=>{try{await fetch(base);return false;}catch{return true;}});await context.close();
 const migrated=path.join(output,'migration-workspace');fs.mkdirSync(migrated);child=spawn(executable,['--workspace',migrated,'--no-browser'],{windowsHide:true,stdio:'ignore'});
 const runtime2=path.join(migrated,'.v2-runtime.json');await poll(()=>fs.existsSync(runtime2));const rt=JSON.parse(fs.readFileSync(runtime2));base='http://127.0.0.1:'+rt.port;
 await poll(async()=>{try{return(await fetch(base)).ok;}catch{return false;}});
 const migrationContext=await browser.newContext({viewport:{width:1440,height:1000}});page=await migrationContext.newPage();await page.goto(base+'/#token='+rt.token);await poll(()=>page.getByRole('button',{name:'刷新',exact:true}).isEnabled());assert.equal((await data('health')).source_commit,bundled.source_commit);
 await nav('数据接入');await page.getByLabel('旧数据路径',{exact:true}).fill(path.join(fixtures,'legacy'));await click('检查旧数据');await page.getByTestId('migration-report').waitFor();await click('复制迁移到当前空工作区');await poll(async()=>(await data('students')).length===1);
 const old=await data('students/00999');assert.equal(old.sources.length,1);assert.equal(old.results.length,1);assert.equal(old.results[0].review,'approved');assert.equal(old.status,'delivered');assert(old.delivered);
 for(const[n,bytes]of legacyFiles)assert.deepEqual(fs.readFileSync(path.join(fixtures,'legacy',n)),bytes);
 await nav('学生与审核');await page.getByTestId('student-table').getByRole('button',{name:'查看流程 / 审核'}).click();await page.getByText('版本与操作记录',{exact:true}).click();await screenshot('10-legacy-migrated');ok('Legacy copies original/result/approved evidence/stages/delivery into empty EXE workspace; source unchanged');
 assert.deepEqual(evidence.errors,[]);
 ok('No uncaught browser page errors');
})().catch(async e=>{evidence.failure=e.stack;console.error(e);if(page)await screenshot('failure').catch(()=>{});process.exitCode=1;}).finally(async()=>{
 fs.writeFileSync(path.join(output,'acceptance.json'),JSON.stringify(evidence,null,2));
 if(page&&base)await page.request.post(base+'/api/v1/shutdown').catch(()=>{});
 if(browser)await browser.close();
 if(mock)mock.close();
 if(child)child.unref();
});
