// Real compiled Vue + FastAPI; prepare_export_dialog.py creates synthetic approved JPEGs.
// node tests/export-dialog.cjs <evidence-dir> [single|bulk]
const {chromium}=require('playwright'),fs=require('fs'),path=require('path'),assert=require('assert');
(async()=>{
 const root=process.argv[2],mode=process.argv[3]||'all',base='http://127.0.0.1:19047';
 const fixture=JSON.parse(fs.readFileSync(path.join(root,'fixture.json'),'utf8'));
 const browser=await chromium.launch({headless:true}),p=await browser.newPage({viewport:{width:1366,height:768}}),errors=[],checks=[],posts=[];
 p.setDefaultTimeout(7000);p.on('pageerror',e=>errors.push(e.message));
 p.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/v1/deliveries'))posts.push(r.postDataJSON());});
 const api='/cohorts/'+fixture.cohort+'/api/v1/';
 const req=async(url,data,method)=>{const r=await p.request.fetch(base+url,{method:method||(data?'POST':'GET'),data});assert(r.ok(),await r.text());return r.json();};
 const shot=async name=>{await p.mouse.move(1,1);await p.waitForTimeout(350);await p.screenshot({path:path.join(root,name+'.png'),animations:'disabled'});};
 const wait=async fn=>{for(let i=0;i<100;i++){if(await fn())return;await p.waitForTimeout(100);}throw Error('condition timeout');};
 const dialog=()=>p.getByRole('dialog',{name:/^(选择导出格式并预览文件名|预览交付文件名)$/});
 const choose=async(text)=>{await dialog().locator('.el-select__wrapper').filter({has:p.getByLabel('导出格式',{exact:true})}).click();await p.getByRole('option',{name:text,exact:true}).click();};
 const named='姓名格式 · {student_id}-{name}{ext} · v2';
 const visibleFooter=async()=>{const d=dialog();await wait(()=>d.evaluate(el=>el.getAnimations().every(a=>a.playState==='finished')));const b=await d.boundingBox(),foot=await d.getByRole('button',{name:'确认生成交付包',exact:true}).boundingBox();assert(b.y>=0&&b.y+b.height<=p.viewportSize().height+1,JSON.stringify({b,viewport:p.viewportSize()}));assert(foot.y>=b.y&&foot.y+foot.height<=p.viewportSize().height);};
 const download=async(batch,name)=>{const r=await p.request.get(base+api+'deliveries/'+batch.id+'/download');assert(r.ok());fs.writeFileSync(path.join(root,name),await r.body());};
 const detail=async sid=>{await p.getByRole('menuitem',{name:'学生与审核',exact:true}).click();await p.getByPlaceholder('搜索学号',{exact:true}).fill(sid);await p.getByRole('button',{name:'查看流程 / 审核',exact:true}).click();await p.getByTestId('review-stage').waitFor();};
 const openSingle=async()=>{await p.getByRole('button',{name:'生成单人交付包',exact:true}).click();await dialog().waitFor();await wait(async()=>(await dialog().innerText()).includes('.jpg'));};
 const cancel=async()=>{await dialog().getByRole('button',{name:'取消',exact:true}).click();await dialog().waitFor({state:'hidden'});};
 try{
 await p.goto(base);await p.getByPlaceholder('访问令牌',{exact:true}).fill(fs.readFileSync(path.join(root,'token.txt'),'utf8'));await p.getByRole('button',{name:'登录',exact:true}).click();await p.getByRole('menuitem',{name:'学生与审核',exact:true}).waitFor();
 if(mode==='all'||mode==='single'){
 for(const b of await req(api+'deliveries'))if(b.status==='prepared'&&b.items.length===1&&b.items[0].student_id==='00123')await req(api+'deliveries/'+b.id+'/cancel',{});
 await detail('00123');let before=(await req(api+'deliveries')).length;
 await openSingle();assert((await dialog().innerText()).includes('默认学号格式'));assert((await dialog().innerText()).includes('00123.jpg'));
 await dialog().locator('.el-select__wrapper').filter({has:p.getByLabel('导出格式',{exact:true})}).click();assert.equal(await p.getByRole('option').count(),3);assert.equal(await p.getByRole('option',{name:/停用格式/}).count(),0);await p.keyboard.press('Escape');
 await choose(named);await wait(async()=>(await dialog().innerText()).includes('00123-张三.jpg'));await visibleFooter();await shot('01-single-selected');
 await cancel();assert.equal((await req(api+'deliveries')).length,before);
 await openSingle();await choose(named);await wait(async()=>(await dialog().innerText()).includes('00123-张三.jpg'));
 await p.route('**/api/v1/deliveries',async route=>{if(route.request().method()==='POST')await new Promise(r=>setTimeout(r,350));await route.continue();});
 const confirm=dialog().getByRole('button',{name:'确认生成交付包',exact:true});
 await confirm.evaluate(b=>{b.click();b.click();});await dialog().waitFor({state:'hidden'});
 await wait(async()=>(await req(api+'deliveries')).length===before+1);
 assert.equal(posts.length,1);assert.equal(posts[0].profile_id,'named');
 const batch=(await req(api+'deliveries'))[0];assert.equal(batch.format_snapshot.profile_id,'named');assert.equal(batch.items[0].file_name,'00123-张三.jpg');
 const uiDownload=p.waitForEvent('download');await p.getByRole('button',{name:'下载交付包',exact:true}).click();await (await uiDownload).saveAs(path.join(root,'single-selected.zip'));await p.unroute('**/api/v1/deliveries');await shot('02-single-generated');
 const duplicate=await p.request.post(base+api+'deliveries',{data:{student_ids:['00123'],profile_id:'default-student-id'}});assert.equal(duplicate.status(),409);
 // Detail may stay open to download, or successful flow can navigate to deliveries.
 const detailDialog=p.getByRole('dialog',{name:'学生 00123 · 流程与审核',exact:true});if(await detailDialog.isVisible())await detailDialog.locator('.el-dialog__headerbtn').click();
 await p.getByRole('menuitem',{name:'照片交付',exact:true}).click();
 await p.getByTestId('delivery').filter({hasText:batch.id.slice(0,16)}).waitFor();assert((await p.getByTestId('delivery').filter({hasText:batch.id.slice(0,16)}).innerText()).includes('姓名格式 v2'));await shot('03-delivery-format');
 await detail('00124');await openSingle();await choose(named);await wait(async()=>(await dialog().innerText()).includes('缺少字段：name'));assert(await dialog().getByRole('button',{name:'确认生成交付包',exact:true}).isDisabled());await shot('04-missing-name');await cancel();
 await p.getByRole('dialog',{name:'学生 00124 · 流程与审核',exact:true}).locator('.el-dialog__headerbtn').click();
 checks.push('single: active/default/select/preview/cancel/one POST/real ZIP/snapshot/prepared guard/missing name');
 }
 if(mode==='all'||mode==='bulk'){
 for(const b of await req(api+'deliveries'))if(b.status==='prepared'&&b.items.some(i=>i.student_id.startsWith('002')))await req(api+'deliveries/'+b.id+'/cancel',{});
 await p.getByRole('menuitem',{name:'学生与审核',exact:true}).click();await p.getByPlaceholder('搜索学号',{exact:true}).fill('002');
 await p.locator('label').filter({has:p.getByRole('checkbox',{name:'全选筛选结果',exact:true})}).click();
 await p.getByRole('button',{name:'预览并生成照片交付包',exact:true}).click();await dialog().waitFor();
 await wait(async()=>await dialog().locator('.el-table__body-wrapper tbody tr').count()>0);
 assert.equal(await dialog().locator('.el-table__body-wrapper tbody tr').count(),20);assert((await dialog().innerText()).includes('共 70 条'));assert((await dialog().innerText()).includes('第 1–20 条'));await visibleFooter();await shot('05-bulk-page1');
 await dialog().locator('.btn-next').click();assert((await dialog().innerText()).includes('第 21–40 条'));await choose(named);
 await wait(async()=>(await dialog().innerText()).includes('合成姓名'));await visibleFooter();
 await dialog().locator('.el-pagination__sizes .el-select__wrapper').click();await p.getByRole('option',{name:/50/}).click();
 await wait(async()=>await dialog().locator('.el-table__body-wrapper tbody tr').count()===50);await visibleFooter();await shot('06-bulk-page50');
 await dialog().locator('.btn-next').click();assert((await dialog().innerText()).includes('第 51–70 条'));await visibleFooter();
 await choose('默认学号格式 · {student_id}{ext} · v1');await wait(async()=>await dialog().locator('.el-table__body-wrapper tbody tr').count()===50);assert((await dialog().innerText()).includes('第 1–50 条'));
 for(const viewport of [{width:1280,height:720},{width:1024,height:600}]){await p.setViewportSize(viewport);await visibleFooter();}await shot('08-short-window');await p.setViewportSize({width:1366,height:768});
 await cancel();
 // New selection and existing processing-plan pager reuse the same default/options.
 await p.getByPlaceholder('搜索学号',{exact:true}).fill('00200');await p.locator('label').filter({has:p.getByRole('checkbox',{name:'全选筛选结果',exact:true})}).click();
 await p.getByRole('button',{name:'预览并生成照片交付包',exact:true}).click();await dialog().waitFor();await wait(async()=>(await dialog().innerText()).includes('共 1 条'));await visibleFooter();await shot('07-small-preview');await cancel();
 await p.getByRole('button',{name:'预览处理计划',exact:true}).click();const plan=p.getByRole('dialog',{name:'本次执行计划',exact:true});await plan.waitFor();assert.equal(await plan.locator('.el-pagination').count(),1);await plan.getByRole('button',{name:'取消',exact:true}).click();
 // Pagination changes only the display: submit from the second page, package all 70.
 await p.getByPlaceholder('搜索学号',{exact:true}).fill('002');await p.locator('label').filter({has:p.getByRole('checkbox',{name:'全选筛选结果',exact:true})}).click();
 await p.getByRole('button',{name:'预览处理计划',exact:true}).click();await plan.waitFor();assert.equal(await plan.locator('.el-table__body-wrapper tbody tr').count(),20);await plan.locator('.btn-next').click();assert((await plan.innerText()).includes('第 21–40 条'));await plan.getByRole('button',{name:'重新计算计划',exact:true}).click();await wait(async()=>(await plan.innerText()).includes('第 1–20 条'));await plan.getByRole('button',{name:'取消',exact:true}).click();
 await p.getByRole('button',{name:'预览并生成照片交付包',exact:true}).click();await dialog().waitFor();await choose(named);await wait(async()=>(await dialog().innerText()).includes('合成姓名'));await dialog().locator('.btn-next').click();
 const expected=await req(api+'deliveries/preview',{student_ids:fixture.ids.filter(id=>id.startsWith('002')),profile_id:'named'});fs.writeFileSync(path.join(root,'batch-preview.json'),JSON.stringify(expected,null,2));
 await dialog().getByRole('button',{name:'确认生成交付包',exact:true}).click();await dialog().waitFor({state:'hidden'});const batch=(await req(api+'deliveries'))[0];assert.equal(batch.items.length,70);await download(batch,'batch-selected.zip');
 checks.push('bulk: 70 rows, 20/50 pagination, range, format switch, selection reset, fixed footer, small list, processing-plan pager');
 }
 if(mode==='all'||mode==='edge'){
 await detail('00125');await openSingle();await choose(named);await wait(async()=>(await dialog().innerText()).includes('00125-张_三.jpg'));assert((await dialog().innerText()).includes('替换'));await shot('09-illegal-character');
 const before=(await req(api+'deliveries')).length;
 // A disabled saved profile must fail on confirmation, not silently use the default.
 await req(api+'export-profiles/named/status',{status:'inactive'});await dialog().getByRole('button',{name:'确认生成交付包',exact:true}).click();await wait(async()=>(await dialog().innerText()).includes('停用'));assert.equal((await req(api+'deliveries')).length,before);assert(await dialog().getByRole('button',{name:'确认生成交付包',exact:true}).isDisabled());
 await req(api+'export-profiles/named/status',{status:'active'});await cancel();await openSingle();
 // Delay old-format response, then switch back: late response must not overwrite it.
 let released;const gate=new Promise(resolve=>released=resolve);let pending=false;
 await p.route('**/api/v1/deliveries/preview',async route=>{if(route.request().postDataJSON().profile_id==='named'){pending=true;await gate;}await route.continue();});
 await choose(named);await wait(async()=>pending);await choose('默认学号格式 · {student_id}{ext} · v1');await wait(async()=>(await dialog().innerText()).includes('00125.jpg'));released();await p.waitForTimeout(400);assert(!(await dialog().innerText()).includes('00125-张_三.jpg'));await p.unroute('**/api/v1/deliveries/preview');
 // Actual request failure is visible and cannot create a batch.
 await p.route('**/api/v1/deliveries/preview',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({message:'模拟预览服务失败'})}));await choose(named);await wait(async()=>(await dialog().innerText()).includes('模拟预览服务失败'));assert(await dialog().getByRole('button',{name:'确认生成交付包',exact:true}).isDisabled());await p.unroute('**/api/v1/deliveries/preview');await cancel();
 await p.getByRole('dialog',{name:'学生 00125 · 流程与审核',exact:true}).locator('.el-dialog__headerbtn').click();
 await p.getByPlaceholder('搜索学号',{exact:true}).fill('0012');
 for(const sid of ['00125','00126'])await p.locator('label').filter({has:p.getByRole('checkbox',{name:'选择学生 '+sid,exact:true})}).click();
 await p.getByRole('button',{name:'预览并生成照片交付包',exact:true}).click();await dialog().waitFor();await choose('仅姓名 · {name}{ext} · v2');await wait(async()=>(await dialog().innerText()).includes('文件名重复'));assert(await dialog().getByRole('button',{name:'确认生成交付包',exact:true}).isDisabled());await shot('10-collision');
 // Removing a selected student invalidates the preview instead of leaving an empty valid page.
 const recycle='/api/v1/recycle-bin/'+fixture.cohort,impact=await req(recycle+'/preview',{student_ids:['00126']});const moved=await req(recycle+'/move',{student_ids:impact.student_ids,revision:impact.revision,confirmed:true});
 try{await choose(named);await wait(async()=>(await dialog().innerText()).includes('00126 不属于当前届次'));assert(await dialog().getByRole('button',{name:'确认生成交付包',exact:true}).isDisabled());}
 finally{await req(recycle+'/'+moved.id+'/restore',{});}
 await cancel();
 const other='/cohorts/'+fixture.other_cohort+'/api/v1/';assert.equal((await p.request.post(base+other+'deliveries',{data:{student_ids:['00125'],profile_id:'named'}})).status(),409);
 assert.equal((await req(api+'deliveries')).length,before);
 checks.push('edge: illegal-character warning, inactive revalidation, late response guard, request failure blocks, sanitized name collision');
 }
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(root,'browser-'+mode+'.json'),JSON.stringify({checks,posts,errors},null,2));console.log(checks);
 }catch(e){await shot('failure-'+mode);throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
