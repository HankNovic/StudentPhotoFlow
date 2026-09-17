// Independent local/ Docker server only. Fixtures are created through authenticated APIs.
// Usage: node tests/review-interaction.cjs <private-test-directory> [base-url]
const {chromium}=require('playwright'),fs=require('fs'),assert=require('assert'),path=require('path');
(async()=>{
 const root=process.argv[2],base=process.argv[3]||'http://127.0.0.1:18996';
 assert(root&&/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base),'Only an explicitly local test server is allowed');
 const browser=await chromium.launch({headless:true}),p=await browser.newPage({viewport:{width:1440,height:1100}}),checks=[],errors=[];
 p.on('pageerror',e=>errors.push(e.message));
 await p.addInitScript(()=>{Object.defineProperty(Crypto.prototype,'randomUUID',{configurable:true,value:undefined});});
 const poll=async(fn,label='condition')=>{for(let i=0;i<150;i++){if(await fn())return;await p.waitForTimeout(150);}throw Error('Timeout: '+label);};
 const req=async(endpoint,data,method)=>{const r=await p.request.fetch(base+endpoint,{method:method||(data?'POST':'GET'),data});assert(r.ok(),await r.text());return r.json();};
 const ok=text=>{checks.push(text);console.log('PASS',text);fs.writeFileSync(path.join(root,'review-browser-results.json'),JSON.stringify({checks,pageErrors:errors},null,2));};
 const menu=async name=>p.getByRole('menuitem',{name,exact:true}).click();
 let cid,api,d;
 const button=name=>d.getByRole('button',{name,exact:true}).and(d.locator('button'));
 const click=async name=>button(name).click();
 const absent=async names=>{for(const name of names)assert.equal(await button(name).count(),0,name+' should be absent');};
 const notice=async text=>poll(async()=>(await d.getByTestId('review-notice').innerText()).includes(text),text);
 const help=async(label,text)=>{await d.getByRole('button',{name:label+'说明',exact:true}).hover();await p.getByRole('tooltip').filter({hasText:text}).last().waitFor({state:'visible'});await p.mouse.move(1,1);};
 const shot=async name=>{await p.mouse.move(1,1);if(!name.includes('running'))await p.waitForTimeout(3300);const bounds=await d.boundingBox();await p.setViewportSize({width:1440,height:Math.max(1100,Math.ceil(bounds.height)+220)});await d.evaluate(e=>{e.closest('.el-overlay-dialog').scrollTop=0;});await p.screenshot({path:path.join(root,name+'.png'),fullPage:true});await p.setViewportSize({width:1440,height:1100});};
 const open=async sid=>{await menu('学生与审核');await p.getByPlaceholder('搜索学号',{exact:true}).fill(sid);await p.getByRole('button',{name:'查看流程 / 审核',exact:true}).click();d=p.getByRole('dialog',{name:'学生 '+sid+' · 流程与审核',exact:true});await d.getByTestId('review-stage').waitFor();};
 const close=async()=>{await p.keyboard.press('Escape');await d.waitFor({state:'hidden'});};
 const choosePhoto=async name=>{await d.locator('input[type=file]').setInputFiles([]);await d.locator('input[type=file]').setInputFiles(path.join(root,name));};
 const photo=fs.readFileSync(path.join(root,'synthetic.png'));
 const uploadAPI=async sid=>{const r=await p.request.post(base+api+'students/'+sid+'/photos',{multipart:{photo:{name:'synthetic.png',mimeType:'image/png',buffer:photo}}});assert(r.ok(),await r.text());};
 const processAPI=async sid=>{const saved=await req('/api/v1/config'),config={...saved.defaults,...saved.saved};const plan=await req(api+'processing-jobs',{student_ids:[sid],config,dry_run:true});const job=await req(api+'processing-jobs',{student_ids:[sid],config,dry_run:false,expected_revision:plan.revision});await poll(async()=>(await req(api+'jobs')).find(j=>j.id===job.id)?.status==='completed','process fixture');};
 try{
  await p.goto(base);await p.getByPlaceholder('访问令牌',{exact:true}).fill(fs.readFileSync(path.join(root,'token.txt'),'utf8'));await p.getByRole('button',{name:'登录',exact:true}).click();await p.getByRole('menuitem',{name:'系统设置',exact:true}).waitFor();
  let settings=await req('/api/v1/settings'),year=2180;while(settings.cohorts.some(c=>c.year===String(year)))year++;
  settings.cohorts.push({year:String(year)});settings=await req('/api/v1/settings',settings,'PUT');cid=settings.cohorts.find(c=>c.year===String(year)).id;api='/cohorts/'+cid+'/api/v1/';
  await req(api+'roster',{student_ids:['00001','00002','00003','00004','00005','00006']});
  await req('/api/v1/config',{quality_enabled:false,background_mode:'none',crop_enabled:true,crop_width:96,crop_height:128},'PUT');
  await p.reload();await menu('学生与审核');await p.locator('.sidebar .el-select__wrapper').click();await p.getByRole('option',{name:year+'级',exact:true}).click();
  await open('00001');await help('上传照片','不会自动开始处理');await absent(['启动替换流程','检查处理计划','开始生成正式成片','试处理预览','审核通过当前正式成片','退回当前正式成片','撤回当前审核','生成单人交付包']);
  assert.equal(await d.getByText('明确生成新处理版本',{exact:true}).count(),0);assert.equal(await d.getByTestId('related-jobs').locator('.el-collapse-item__header').getAttribute('aria-expanded'),'false');await shot('review-01-missing');ok('missing photo stage, tooltips and both inert option removal/history default collapse');
  const photos=base+api+'students/00001/photos';
  await p.route(photos,r=>r.fulfill({status:409,contentType:'application/json',body:JSON.stringify({message:'SIMULATED 上传被业务规则阻止'})}));
  await choosePhoto('synthetic.png');await notice('SIMULATED 上传被业务规则阻止');assert.equal((await req(api+'students/00001')).sources.length,0);await p.unroute(photos);ok('upload callback catches backend failure visibly without unhandled rejection');
  let releaseUpload;const uploadWait=new Promise(r=>releaseUpload=r);let uploads=0;
  await p.route(photos,async r=>{uploads++;await uploadWait;await r.continue();});
  await choosePhoto('synthetic.png');await poll(async()=>await button('上传照片').evaluate(e=>e.classList.contains('is-loading')),'upload loading');assert(await button('上传照片').isDisabled());releaseUpload();await notice('原始照片已保存');await p.unroute(photos);assert.equal(uploads,1);
  await poll(async()=>await button('检查处理计划').count()===1);await absent(['启动替换流程','审核通过当前正式成片','撤回当前审核','生成单人交付包']);await help('检查处理计划','不会创建任务');await help('试处理预览','不能直接审核');await shot('review-02-original');
  await choosePhoto('synthetic.png');await notice('照片内容未变化，未新增版本');assert.equal((await req(api+'students/00001')).sources.length,1);ok('upload loading/dedup feedback and original-only stage');
  const processing=base+api+'processing-jobs';let captured=[];
  // Test-only injected parameter change while a real plan response is delayed.
  await d.getByText('仅本次调整处理参数',{exact:true}).click();await help('仅本次调整处理参数','不修改全局配置');await absent(['添加当前地址到历史']);await help('测试当前 API','不上传照片');
  let releasePlan,planCalls=0;const planWait=new Promise(r=>releasePlan=r);
  await p.route(processing,async r=>{planCalls++;const response=await r.fetch();await planWait;await r.fulfill({response});});
  await button('检查处理计划').evaluate(e=>{e.click();e.click();});await poll(async()=>planCalls===1);assert(await button('检查处理计划').isDisabled());
  await d.locator('[data-config="background_color"] input').evaluate(e=>{e.value='#123456';e.dispatchEvent(new Event('input',{bubbles:true}));});
  releasePlan();await poll(async()=>!await button('检查处理计划').isDisabled());assert(await button('开始生成正式成片').isDisabled());assert.equal(planCalls,1);await p.unroute(processing);
  await d.locator('[data-config="background_color"] input').fill('#438EDB');await d.getByText('仅本次调整处理参数',{exact:true}).click();
  assert.equal((await req('/api/v1/config')).saved.background_color,'#438EDB');ok('double plan click sends once; late plan discarded after injected config change; global config unchanged');
  await p.route(processing,async r=>{captured.push(r.request().postDataJSON());await r.continue();});
  await click('检查处理计划');await poll(async()=>await button('开始生成正式成片').isEnabled());await help('开始生成正式成片','仍需人工审核');
  const previewEndpoint=base+api+'students/00001/preview';let releasePreview;const previewWait=new Promise(r=>releasePreview=r);
  await p.route(previewEndpoint,async r=>{await previewWait;await r.continue();});await click('试处理预览');await poll(async()=>await button('试处理预览').evaluate(e=>e.classList.contains('is-loading')));assert(await button('检查处理计划').isDisabled());releasePreview();await notice('试处理预览已完成');await p.unroute(previewEndpoint);assert.equal((await req(api+'students/00001')).results.length,0);await absent(['审核通过当前正式成片']);assert(await button('开始生成正式成片').isDisabled());
  await p.route(previewEndpoint,r=>r.fulfill({status:409,contentType:'application/json',body:'{"message":"SIMULATED 试处理失败"}'}));await click('试处理预览');await notice('SIMULATED 试处理失败');assert.equal(await d.getByText('临时预览，不参与审核',{exact:true}).count(),0);await p.unroute(previewEndpoint);ok('preview loading, actual local processing, no formal review and failure clears old preview');
  await click('检查处理计划');await poll(async()=>await button('开始生成正式成片').isEnabled());
  // Existing plan must disappear on a failed recheck.
  await p.unroute(processing);await p.route(processing,r=>r.fulfill({status:409,contentType:'application/json',body:'{"message":"SIMULATED 计划校验失败"}'}));await click('检查处理计划');await notice('SIMULATED 计划校验失败');assert(await button('开始生成正式成片').isDisabled());await p.unroute(processing);
  // No secure RNG: no request leaves the page and no executable plan remains.
  await p.evaluate(()=>{window.savedRandomValues=Crypto.prototype.getRandomValues;Object.defineProperty(Crypto.prototype,'getRandomValues',{configurable:true,value:undefined});});
  let noRandomCalls=0;await p.route(processing,r=>{noRandomCalls++;return r.continue();});await click('检查处理计划');await notice('浏览器不支持安全随机数');assert.equal(noRandomCalls,0);assert(await button('开始生成正式成片').isDisabled());await p.unroute(processing);
  await p.evaluate(()=>Object.defineProperty(Crypto.prototype,'getRandomValues',{configurable:true,value:window.savedRandomValues}));
  await p.route(processing,async r=>{captured.push(r.request().postDataJSON());await r.continue();});await click('检查处理计划');await poll(async()=>await button('开始生成正式成片').isEnabled());await click('开始生成正式成片');await notice('正式处理任务已创建');await poll(async()=>await button('审核通过当前正式成片').count()===1);await p.unroute(processing);
  const started=captured.find(x=>x.dry_run===false);assert.match(started.request_id,/^[a-f0-9-]{36}$/);assert(captured.every(x=>!('new_version' in x)));ok('UUID fallback used for real formal job; no RNG fails closed; failed plan cleared; no force-version sent');
  await help('审核通过','之后'.replace('之后','保存后'));await help('退回','不会自动重新处理');await absent(['启动替换流程','生成单人交付包','撤回当前审核']);await shot('review-03-await-review');
  await click('退回当前正式成片');await p.getByRole('dialog',{name:'退回当前正式成片',exact:true}).getByRole('textbox').fill('独立合成测试退回');await p.getByRole('button',{name:'保存退回',exact:true}).click();await notice('未自动重新处理');await poll(async()=>await button('撤回当前审核').count()===1);await absent(['审核通过当前正式成片','退回当前正式成片','启动替换流程']);await help('撤回审核','恢复为待审核');
  await click('检查处理计划');await notice('同一原图和参数已有结果');assert(await button('开始生成正式成片').isDisabled());await click('撤回当前审核');await notice('审核决定已撤回');await poll(async()=>await button('审核通过当前正式成片').count()===1);ok('reject/undo stages and unchanged same-result processing protection');
  // Commit is real; only following GETs fail, so UI must report partial success.
  let failReads=false;const studentEndpoint=base+api+'students/00001';
  await p.route(base+api+'reviews',async r=>{const response=await r.fetch();failReads=true;await r.fulfill({response});});
  await p.route(studentEndpoint,r=>failReads?r.fulfill({status:503,contentType:'application/json',body:'{"message":"SIMULATED 刷新服务暂不可用"}'}):r.continue());
  await click('审核通过当前正式成片');await notice('操作已提交，但页面刷新失败');assert.equal((await req(api+'students/00001')).status,'approved');failReads=false;await p.unroute(studentEndpoint);await p.unroute(base+api+'reviews');await click('刷新详情');await poll(async()=>await button('生成单人交付包').count()===1);await absent(['启动替换流程','审核通过当前正式成片','退回当前正式成片','更换原始照片','检查处理计划']);await help('生成交付包','实际交给学生后还需确认');await shot('review-04-approved');ok('approved stage and committed-success/refresh-failure distinction');
  await click('生成单人交付包');await notice('已生成单人交付包');await poll(async()=>await button('下载交付包').count()===1);await absent(['生成单人交付包','撤回当前审核','启动替换流程']);await help('下载交付包','不会自动把学生标记为已交付');await help('确认已交付','实际交给学生');await shot('review-05-prepared');
  const [download]=await Promise.all([p.waitForEvent('download'),click('下载交付包')]);await download.saveAs(path.join(root,'review-delivery.zip'));assert.equal((await req(api+'students/00001')).status,'approved');
  await click('确认已交付');await p.getByRole('dialog',{name:'确认已交付',exact:true}).getByRole('button',{name:'确定',exact:true}).click();await notice('已记录实际交付');await poll(async()=>await button('启动替换流程').count()===1);await absent(['审核通过当前正式成片','退回当前正式成片','生成单人交付包','更换原始照片','检查处理计划']);await help('启动替换流程','上传照片并重新处理');await shot('review-06-delivered');ok('prepared/download/confirm/delivered stages preserve actual delivery distinction');
  await click('启动替换流程');await p.getByRole('dialog',{name:'启动照片替换',exact:true}).getByRole('textbox').fill('独立测试重新制作');await p.getByRole('button',{name:'启动替换',exact:true}).click();await notice('替换流程已启动，请上传新照片');await poll(async()=>await button('上传替换照片').count()===1);await absent(['启动替换流程']);await choosePhoto('replacement.png');await notice('原始照片已保存');assert.equal((await req(api+'students/00001')).sources.length,2);assert((await req(api+'students/00001')).delivered);await close();ok('delivered replacement entry uses existing API and preserves historical delivery');
  // Build two real review records, then audit the middle student under a status filter.
  for(const sid of ['00002','00003']){await uploadAPI(sid);await processAPI(sid);}
  await p.getByPlaceholder('搜索学号',{exact:true}).fill('');await p.getByRole('button',{name:'刷新',exact:true}).click();await p.getByRole('button',{name:/待人工审核/}).first().click();await p.getByTestId('student-table').getByRole('row').filter({hasText:'00002'}).getByRole('button',{name:'查看流程 / 审核',exact:true}).click();d=p.getByRole('dialog',{name:'学生 00002 · 流程与审核',exact:true});await button('审核通过当前正式成片').waitFor();await click('审核通过当前正式成片');await notice('审核通过');await click('下一张');d=p.getByRole('dialog',{name:'学生 00003 · 流程与审核',exact:true});await d.getByTestId('review-stage').waitFor();assert.equal((await req(api+'students/00003')).status,'review');await close();ok('audit removal from live filter retains original next-student order; navigation does not audit');
  await p.locator('.filter .el-select__wrapper').click();await p.getByRole('option',{name:'全部状态',exact:true}).click();
  // Real asynchronous task using controllable local mock, never a production service.
  await uploadAPI('00006');await req('/api/v1/config',{quality_enabled:false,background_mode:'hivision',hivision_url:'http://127.0.0.1:18997',hivision_timeout:20,hivision_concurrency:1},'PUT');
  const mock=await p.request.post('http://127.0.0.1:18997/control',{data:{delay:5,modes:[]}});assert(mock.ok());await open('00006');await click('检查处理计划');await poll(async()=>await button('开始生成正式成片').isEnabled());await click('开始生成正式成片');await notice('正式处理任务已创建');await poll(async()=>(await d.getByTestId('review-stage').innerText()).includes('运行中'));await absent(['审核通过当前正式成片','生成单人交付包','检查处理计划','上传照片','更换原始照片','启动替换流程']);assert.equal(await d.getByTestId('related-jobs').locator('.el-collapse-item__header').getAttribute('aria-expanded'),'false');await shot('review-07-running');
  await poll(async()=>await button('审核通过当前正式成片').count()===1);assert.equal(await d.getByTestId('related-jobs').locator('.el-collapse-item__header').getAttribute('aria-expanded'),'false');await d.getByTestId('related-jobs').locator('.el-collapse-item__header').click();await d.getByText('整批已结束项目',{exact:false}).waitFor();await shot('review-08-history');await close();ok('real async simulated service: locked stage and collapsed history continue polling to review');
  await menu('处理配置');assert(await p.getByRole('button',{name:'添加当前地址到历史',exact:true}).isVisible());assert(await p.getByRole('spinbutton',{name:'请求并发数',exact:true}).isEnabled());ok('global config retains address-history controls and editable concurrency; local settings do not');
  assert.deepEqual(errors,[]);ok('no uncaught browser errors; all fixtures isolated, no production calls');
 }catch(e){await p.screenshot({path:path.join(root,'review-failure.png'),fullPage:true});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
