// Actual Edge + disposable copy of real market DB. Lab training writes only its
// sidecar under that test directory; synthetic predictions are explicitly labeled.
const {test}=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require('@playwright/test');
const fs=require('node:fs');
const base=process.env.HONGZHOU_CHART_LAB_URL||'http://127.0.0.1:18893';
test('shared product state, 18 period combinations, one-point/empty charts and lab training',async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage(),errors=[],requests=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
 try{
  const payload=await (await context.request.get(base+'/api/dashboard/chart')).json();
  await page.goto(base+'/market-brief');await page.waitForFunction(()=>document.documentElement.dataset.dashboardReady==='1'&&document.documentElement.dataset.dashboardPredictionReady==='1');
  const combinations=[];
  for(const id of ['fh_scrap','fh_rebar','fh_sections']){
   const s=payload.series.find(x=>x.id===id),latest=s.points.at(-1);
   await page.locator('#chartInstrument').selectOption(id);
   for(const period of ['7','30','90','180','365','0']){
    await page.locator('#period').selectOption(period);
    const expected=s.points.filter(p=>!Number(period)||Date.parse(p.date)>=Date.parse(payload.as_of.slice(0,10))-Number(period)*86400000);
    assert.equal(await page.locator('#fhLatest').getAttribute('data-instrument'),id);
    assert.equal(Number(await page.locator('#fhLatest').getAttribute('data-price')),latest.price);
    assert.equal(await page.locator('#fhLatest').getAttribute('data-date'),latest.date);
    assert.ok((await page.locator('#priceTitle').textContent()).includes(s.label));
    assert.ok((await page.locator('#chartMeta').textContent()).includes(s.label));
    assert.equal(await page.locator('#fhLatest a').getAttribute('href'),latest.source_url);
    assert.equal(Number(await page.locator('#fhChart').getAttribute('data-points')),expected.length);
    assert.equal(await page.locator('#fhChart').isVisible(),true);
    if(period==='7'){assert.equal(expected.length,1);assert.match(await page.locator('#chartNote').textContent(),/僅一筆/);assert.match(await page.locator('#chartTooltip').textContent(),new RegExp(latest.date));}
    if(period==='365'||period==='0')assert.equal(expected.length,21);
    await page.locator('#fhChart').scrollIntoViewIfNeeded();
    const box=await page.locator('#fhChart').boundingBox();
    for(let x=25;x<box.width-80;x+=25){await page.mouse.move(box.x+x,box.y+120);await page.waitForTimeout(15);if(/2026-.*TWD/.test(await page.locator('#chartTooltip').textContent()))break;}
    assert.match(await page.locator('#chartTooltip').textContent(),/2026-.*TWD/);
    combinations.push({id,period,points:expected.length,latest:latest.price});
   }
  }
  const jp=payload.series.find(x=>x.label.startsWith('日本'));
  await page.locator('#chartInstrument').selectOption(jp.id);await page.locator('#period').selectOption('7');
  assert.equal(await page.locator('#fhChart').isVisible(),false);assert.match(await page.locator('#chartNote').textContent(),/該期間沒有報價/);
  await page.locator('#showAllHistory').click();assert.equal(await page.locator('#period').inputValue(),'0');assert.equal(await page.locator('#fhChart').isVisible(),true);
  assert.equal(requests.filter(u=>u.includes('/api/dashboard/chart')).length,1);
  assert.equal(requests.filter(u=>u.includes('/update')).length,0);
  fs.mkdirSync('.test-runs/chart-lab-evidence',{recursive:true});
  await page.locator('#chartInstrument').selectOption('fh_rebar');await page.locator('#period').selectOption('7');
  await page.screenshot({path:'.test-runs/chart-lab-evidence/rebar-one-week.png',fullPage:false});
  await page.goto(base+'/prediction-lab');await page.waitForFunction(()=>document.documentElement.dataset.labReady==='1');
  const trained=[];
  for(const [instrument,horizon] of [['demo_scrap','1'],['demo_rebar','2'],['demo_copper','4']]){
   await page.locator('#labInstrument').selectOption(instrument);await page.locator('#labHorizon').selectOption(horizon);
   assert.equal(await page.locator('#predictModel').isDisabled(),true);
   const response=page.waitForResponse(r=>r.url().endsWith('/api/prediction-lab/train'));
   await page.locator('#trainModel').click();const model=await (await response).json();
   await page.waitForFunction(()=>!document.getElementById('predictModel').disabled);
   assert.equal(model.status,'trained');assert.match(await page.locator('#trainingSummary').textContent(),/合成測試/);
   assert.equal(await page.locator('#labMetrics tr').count(),3);assert.equal(await page.locator('#labConfusion tr').count(),3);
   await page.locator('#predictModel').click();await page.waitForFunction(()=>document.getElementById('labStatus').textContent==='推論完成。');
   assert.match(await page.locator('#predictionWarning').textContent(),/不代表任何真實市場/);
   assert.match(await page.locator('#predictionProbabilities').textContent(),/未校準模型分數/);
   trained.push({instrument,horizon,training:model.training_samples,test:model.backtest.samples,accuracy:model.backtest.accuracy,run:model.run_id});
  }
  await page.screenshot({path:'.test-runs/chart-lab-evidence/lab-test-result.png',fullPage:true});
  await page.locator('#labMode').selectOption('real');assert.equal(await page.locator('#labPrediction').isVisible(),false);assert.equal(await page.locator('#predictModel').isDisabled(),true);
  await page.locator('#trainModel').click();await page.waitForFunction(()=>!document.getElementById('trainModel').disabled);
  assert.match(await page.locator('#labStatus').textContent(),/尚無法建立可靠模型/);
  await page.locator('#labMode').selectOption('test');assert.equal(await page.locator('#trainModel').isDisabled(),false);
  assert.deepEqual(errors,[]);console.log(JSON.stringify({combinations,trained,onePoint:true,zeroPeriodRecovery:true,chartDownloads:1,realModeBlockedHonestly:true,errors}));
 }finally{await browser.close();}
});
