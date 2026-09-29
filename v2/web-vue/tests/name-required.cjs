const {chromium}=require('playwright'),assert=require('assert'),fs=require('fs'),path=require('path');

(async()=>{
 const evidence=path.resolve(process.argv[2]),base=process.argv[3]||'http://127.0.0.1:19043';
 const browser=await chromium.launch({headless:true,...(process.env.SPF_BROWSER_CHANNEL?{channel:process.env.SPF_BROWSER_CHANNEL}:{})}),context=await browser.newContext({viewport:{width:1440,height:1000}});
 const token=process.env.SPF_TEST_TOKEN||'browser-test-token';
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(15000);
 try{
  await page.goto(base);
  await page.getByPlaceholder('访问令牌',{exact:true}).fill(token);await page.getByRole('button',{name:'登录',exact:true}).click();
  await page.getByTestId('student-table').waitFor();
  await page.getByRole('menuitem',{name:'数据接入'}).click();const card=page.getByTestId('excel-import'),receive=card.getByRole('button',{name:'接收原始图片'});
  await card.locator('input[type=file]').setInputFiles(path.join(evidence,'missing-name-column.xlsx'));await card.getByRole('button',{name:'检查表格'}).click();
  await page.getByText('未识别到姓名列，请手动选择姓名列后重新检查',{exact:true}).waitFor();assert(await receive.isDisabled());
  await card.screenshot({path:path.join(evidence,'01-missing-name-column.png')});
  await card.locator('input[type=file]').setInputFiles(path.join(evidence,'empty-name.xlsx'));await card.getByRole('button',{name:'检查表格'}).click();
  await card.getByText('第 2 行姓名为空，请补齐后重新检查。',{exact:true}).waitFor();assert(await receive.isDisabled());
  await card.screenshot({path:path.join(evidence,'02-empty-name.png')});
  const settings=await (await page.request.get(base+'/api/v1/settings')).json(),importUrl=base+'/cohorts/'+settings.cohorts[0].id+'/api/v1/imports/xlsx';
  const api=base+'/cohorts/'+settings.cohorts[0].id+'/api/v1/';
  const before=await (await page.request.get(api+'students')).json();
  for(const [file,fields] of [['missing-name-column.xlsx',{}],['empty-name.xlsx',{name_column:'C'}],['whitespace.xlsx',{name_column:'C'}]]){
   const response=await page.request.post(importUrl,{multipart:{file:{name:file,mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:fs.readFileSync(path.join(evidence,file))},inspect_only:'false',...fields}});
   assert.equal(response.status(),409);assert.match(await response.text(),/姓名列|姓名不能为空/);
  }
  assert.deepEqual(await (await page.request.get(api+'students')).json(),before);
  const pair=page.getByTestId('historical').locator('.action-pair'),left=await pair.locator('.el-checkbox').boundingBox(),help=await pair.locator('.action-help').boundingBox();
  assert.equal(Math.round(help.x-(left.x+left.width)),6);assert(Math.abs((left.y+left.height/2)-(help.y+help.height/2))<=1);
  await pair.scrollIntoViewIfNeeded();await page.getByTestId('historical').screenshot({path:path.join(evidence,'03-action-help-alignment.png')});
  for(const [file,sid,name,mapping] of [['valid.xlsx','00803','合成张三',null],['manual.xlsx','00804','合成李四',{学号列:'B',图片列:'A',姓名列:'C'}]]){
   await card.locator('input[type=file]').setInputFiles(path.join(evidence,file));
   if(mapping)for(const [label,value] of Object.entries(mapping))await card.getByRole('textbox',{name:label,exact:true}).fill(value);
   await card.getByRole('button',{name:'检查表格'}).click();
   const choose=page.getByRole('dialog',{name:'选择学生届次'});await choose.getByRole('button',{name:'确定届次'}).click();await choose.waitFor({state:'hidden'});assert(await receive.isEnabled());
   await card.screenshot({path:path.join(evidence,'04-'+sid+'-valid-mapping.png')});
   await receive.click();await page.getByRole('heading',{name:'任务进度',exact:true}).waitFor();
   let student;for(let i=0;i<100;i++){student=await (await page.request.get(api+'students/'+sid)).json();if(student.name===name&&student.sources?.length)break;await page.waitForTimeout(100);}
   assert.equal(student.name,name);assert.equal(student.sources.length,1);
   await page.getByRole('menuitem',{name:'数据接入',exact:true}).click();
  }
  await page.getByRole('menuitem',{name:'学生与审核',exact:true}).click();
  const allHelp=page.getByRole('button',{name:'全选筛选结果说明',exact:true});await allHelp.waitFor();
  assert.equal(await allHelp.evaluate(el=>getComputedStyle(el.parentElement).gap),'6px');
  await page.getByTestId('student-table').locator('tbody tr').filter({hasText:'00123'}).getByRole('button',{name:'查看流程 / 审核',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:/学生/}).first();await dialog.waitFor();
  for(const help of await dialog.locator('.action-help:visible').all()){
   assert.equal(await help.evaluate(el=>getComputedStyle(el.parentElement).gap),'6px');
   assert.equal(await help.evaluate(el=>getComputedStyle(el.parentElement).alignItems),'center');
  }
  await dialog.screenshot({path:path.join(evidence,'05-review-help.png')});
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(evidence,'browser-results.json'),JSON.stringify({checks:['login','missing column and empty name block UI','direct receive including whitespace rejected, students unchanged','auto and manual column mapping, real image accepted and name persisted','historical help 6px centered','student and review help shared style'],errors},null,2));
  console.log('PASS Excel required names, auto/manual mapping and real reception, shared ActionHelp alignment');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
