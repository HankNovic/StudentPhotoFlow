// Compiled Vue + real local FastAPI; synthetic XLSX/JPEG only, no Hivision.
// node tests/export-names.cjs <independent-evidence-dir> [http://127.0.0.1:19043]
const {chromium}=require('playwright'),assert=require('assert'),fs=require('fs'),path=require('path');
(async()=>{
 const root=process.argv[2],base=process.argv[3]||'http://127.0.0.1:19043';
 assert(root&&/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base));
 const browser=await chromium.launch({headless:true}),p=await browser.newPage({viewport:{width:1440,height:1050}}),checks=[],errors=[];
 p.setDefaultTimeout(10000);p.on('pageerror',e=>errors.push(e.message));
 const poll=async(fn,label)=>{for(let n=0;n<120;n++){if(await fn())return;await p.waitForTimeout(150);}throw Error('Timeout: '+label);};
 const req=async(endpoint,data,method)=>{const r=await p.request.fetch(base+endpoint,{method:method||(data?'POST':'GET'),data});assert(r.ok(),await r.text());return r.json();};
 const menu=async(name)=>p.getByRole('menuitem',{name,exact:true}).click();
 const shot=async(name)=>{await p.mouse.move(1,1);await p.waitForTimeout(500);await p.screenshot({path:path.join(root,name+'.png'),fullPage:true,animations:'disabled'});};
 const ok=(text)=>{checks.push(text);console.log('PASS',text);fs.writeFileSync(path.join(root,'browser-results.json'),JSON.stringify({checks,pageErrors:errors},null,2));};
 const chooseCohort=async()=>{await p.getByRole('dialog',{name:'选择学生届次',exact:true}).getByRole('button',{name:'确定届次',exact:true}).click();};
 let api;
 async function select(ids){
  await menu('学生与审核');await p.getByPlaceholder('搜索学号',{exact:true}).fill('');
  const all=p.getByRole('checkbox',{name:'全选筛选结果',exact:true}),label=p.locator('label').filter({has:all});
  if(!await all.isChecked())await label.click();
  await label.click();
  for(const sid of ids){
   const box=p.getByRole('checkbox',{name:'选择学生 '+sid,exact:true});
   if(!await box.isChecked())await p.locator('label').filter({has:box}).click();
   assert(await box.isChecked());
  }
 }
 async function preview(ids){
  await select(ids);await p.getByRole('button',{name:'预览并生成照片交付包',exact:true}).click();
  const d=p.getByRole('dialog',{name:'预览交付文件名',exact:true});await d.waitFor();return d;
 }
 async function download(locator,file){
  const event=p.waitForEvent('download');await locator.click();await (await event).saveAs(path.join(root,file));
 }
 try{
  await p.goto(base);await p.getByPlaceholder('访问令牌',{exact:true}).fill(fs.readFileSync(path.join(root,'token.txt'),'utf8'));
  await p.getByRole('button',{name:'登录',exact:true}).click();await p.getByRole('menuitem',{name:'学生与审核',exact:true}).waitFor();
  const settings=await req('/api/v1/settings');api='/cohorts/'+settings.cohorts[0].id+'/api/v1/';
  await req('/api/v1/config',{quality_enabled:false,auto_orient:false,background_mode:'none',crop_enabled:false},'PUT');
  await p.reload();await menu('数据接入');
  const excel=p.getByTestId('excel-import');
  await excel.locator('input[type=file]').setInputFiles(path.join(root,'names.xlsx'));
  await p.getByLabel('学号列',{exact:true}).fill('B');await p.getByLabel('图片列',{exact:true}).fill('D');
  await p.getByLabel('姓名列',{exact:true}).fill('C');
  await excel.getByRole('button',{name:'检查表格',exact:true}).click();await chooseCohort();
  assert.equal(await p.getByLabel('姓名列',{exact:true}).inputValue(),'C','First check honors manual name column');
  await p.getByLabel('姓名列',{exact:true}).fill('');
  await p.getByLabel('学号列',{exact:true}).fill('B');await p.getByLabel('姓名列',{exact:true}).fill('C');
  assert(await excel.getByRole('button',{name:'接收原始图片',exact:true}).isDisabled());
  await excel.getByRole('button',{name:'检查表格',exact:true}).click();await chooseCohort();
  assert((await excel.getByTestId('name-column-result').innerText()).includes('C · 正式姓名'));
  assert((await excel.innerText()).includes('第 5 行姓名为空'));
  await shot('01-excel-name-selection');
  await excel.getByRole('button',{name:'接收原始图片',exact:true}).click();
  await poll(async()=>(await req(api+'students')).length===5,'Excel roster');
  await poll(async()=>(await req(api+'jobs')).every(j=>j.status==='completed'),'Excel import');
  let students=await req(api+'students');assert.equal(students.find(s=>s.id==='00123').name,'张三');
  await menu('学生与审核');await p.getByText('张三',{exact:true}).first().waitFor();await shot('02-students-with-names');
  ok('UI selects ID B/name C, rechecks exact mapping, reports empty row, imports and displays persisted names');
  await menu('数据接入');await excel.locator('input[type=file]').setInputFiles(path.join(root,'conflict.xlsx'));
  await excel.getByRole('button',{name:'检查表格',exact:true}).click();
  await poll(async()=>(await excel.innerText()).includes('姓名冲突：09999'),'conflict feedback');
  assert(await excel.getByRole('button',{name:'接收原始图片',exact:true}).isDisabled());
  assert(!(await req(api+'students')).some(s=>s.id==='09999'));await shot('03-name-conflict');
  // Selecting a different workbook invalidates the previous result.
  await excel.locator('input[type=file]').setInputFiles(path.join(root,'old.xlsx'));
  await excel.getByRole('button',{name:'检查表格',exact:true}).click();await chooseCohort();
  assert.equal(await p.getByLabel('姓名列',{exact:true}).inputValue(),'');
  await excel.getByRole('button',{name:'接收原始图片',exact:true}).click();
  await poll(async()=>(await req(api+'students')).length===6,'legacy Excel');
  await poll(async()=>(await req(api+'jobs')).every(j=>j.status==='completed'),'legacy import done');
  ok('UI conflict blocks import; old workbook without name imports normally');
  await select(['00123','00456','00789','00888','00999','00000']);
  await p.getByRole('button',{name:'预览处理计划',exact:true}).click();
  await p.getByRole('dialog',{name:'本次执行计划',exact:true}).getByRole('button',{name:'按此计划开始处理',exact:true}).click();
  await poll(async()=>(await req(api+'students')).every(s=>s.status==='review'),'real local pipeline');
  await menu('学生与审核');await p.locator('main > header').getByRole('button',{name:'刷新',exact:true}).click();
  await poll(async()=>((await p.getByTestId('student-table').innerText()).match(/待人工审核/g)||[]).length===6,'UI result refresh');
  await select(['00123','00456','00789','00888','00999','00000']);
  await p.getByRole('button',{name:'批量审核通过',exact:true}).click();
  await p.getByRole('dialog',{name:'批量审核通过',exact:true}).getByRole('button',{name:'确认批量审核',exact:true}).click();
  await poll(async()=>(await req(api+'students')).every(s=>s.status==='approved'),'review');
  ok('Synthetic JPEGs run through existing local pipeline and batch review, without Hivision');
  await menu('照片交付');await p.getByRole('button',{name:'新建导出格式',exact:true}).click();
  const form=p.getByRole('dialog',{name:'新建导出格式',exact:true});
  await form.getByLabel('格式名称',{exact:true}).fill('学号与姓名');
  assert.equal(await form.getByRole('button',{name:'添加 {name}',exact:true}).count(),1);
  await form.getByLabel('高级模板',{exact:true}).fill('{student_id}-{name}{ext}');
  await form.getByRole('button',{name:'保存格式',exact:true}).click();await form.waitFor({state:'hidden'});
  const profileRow=p.getByRole('row').filter({hasText:'学号与姓名'});
  await profileRow.getByRole('button',{name:'设为默认',exact:true}).click();
  await poll(async()=>(await req(api+'export-profiles')).some(x=>x.name==='学号与姓名'&&x.is_default),'default profile');
  await shot('04-name-template');
  let d=await preview(['00888']);
  assert((await d.innerText()).includes('缺少字段：name'));
  assert(await d.getByRole('button',{name:'确认生成交付包',exact:true}).isDisabled());await shot('05-missing-name-blocked');
  await d.getByRole('button',{name:'取消',exact:true}).click();
  d=await preview(['00123']);assert((await d.innerText()).includes('00123-张三.jpg'));await shot('06-name-preview');
  await d.getByRole('button',{name:'取消',exact:true}).click();
  ok('UI creates and defaults name template; real filename preview; missing name visibly blocks generation');
  await p.getByPlaceholder('搜索学号',{exact:true}).fill('00123');
  await p.getByRole('button',{name:'查看流程 / 审核',exact:true}).click();
  d=p.getByRole('dialog',{name:'学生 00123 · 流程与审核',exact:true});
  await d.getByTestId('review-stage').waitFor();assert((await d.innerText()).includes('姓名：张三'));
  await d.getByRole('button',{name:'生成单人交付包',exact:true}).click();
  await poll(async()=>(await d.innerText()).includes('已生成单人交付包'),'single package');
  await download(d.getByRole('button',{name:'下载交付包',exact:true}),'single.zip');
  await shot('07-single-delivery');await p.keyboard.press('Escape');await d.waitFor({state:'hidden'});
  let single=(await req(api+'deliveries')).find(b=>b.items.length===1&&b.items[0].student_id==='00123');
  assert.equal(single.items[0].name,'张三');assert.equal(single.items[0].file_name,'00123-张三.jpg');assert.equal(single.status,'prepared');
  ok('UI single detail displays name, generates and downloads single ZIP using shared default format');
  d=await preview(['00456','00789']);
  assert((await d.innerText()).includes('00789-张_三.jpg'));assert((await d.innerText()).includes('非法字符已替换'));
  await shot('08-batch-preview');await d.getByRole('button',{name:'确认生成交付包',exact:true}).click();
  await poll(async()=>(await req(api+'deliveries')).some(b=>b.items.length===2),'batch package');
  const batch=(await req(api+'deliveries')).find(b=>b.items.length===2);
  await download(p.getByTestId('delivery').filter({hasText:batch.id.slice(0,16)}).getByRole('link',{name:'下载照片包',exact:true}),'batch.zip');
  await shot('09-batch-delivery');
  ok('UI batch preview warns on sanitized name and generates/downloads two-photo ZIP');
  d=await preview(['00888']);
  await d.locator('.el-select__wrapper').click();
  await p.getByRole('option',{name:'默认学号格式 · {student_id}{ext}',exact:true}).click();
  await poll(async()=>(await d.innerText()).includes('00888.jpg'),'default old preview');
  assert(await d.getByRole('button',{name:'确认生成交付包',exact:true}).isEnabled());await shot('10-legacy-id-format');
  await d.getByRole('button',{name:'确认生成交付包',exact:true}).click();
  await poll(async()=>(await req(api+'deliveries')).some(b=>b.items[0].student_id==='00888'),'old format package');
  const legacy=(await req(api+'deliveries')).find(b=>b.items[0].student_id==='00888');
  await download(p.getByTestId('delivery').filter({hasText:legacy.id.slice(0,16)}).getByRole('link',{name:'下载照片包',exact:true}),'legacy-id.zip');
  ok('UI switches missing-name student to old ID-only template, generates and downloads valid ZIP');
  const health=await req('/healthz');assert.equal(await p.locator('.app').getAttribute('data-build'),health.build_id);
  assert.equal(await p.locator('.deployment-notice').count(),0);
  fs.writeFileSync(path.join(root,'browser-records.json'),JSON.stringify({students:await req(api+'students'),batches:await req(api+'deliveries'),health},null,2));
  assert.deepEqual(errors,[]);ok('No uncaught browser errors; built Vue and server build IDs match');
 }catch(e){await shot('failure');throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
