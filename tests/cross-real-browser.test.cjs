const {test}=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require('@playwright/test'),fs=require('node:fs');
const base=process.env.HONGZHOU_CROSS_URL||'http://127.0.0.1:18894';
test('real archived steel model A/B/C, audit dates, source inventory and FH gate',async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.goto(base+'/prediction-lab');await page.waitForFunction(()=>document.documentElement.dataset.labReady==='1');
  await page.locator('#labMode').selectOption('cross');await page.locator('#labInstrument').selectOption('cn_release');
  const response=page.waitForResponse(r=>r.url().endsWith('/api/prediction-lab/train'),{timeout:120000});
  await page.locator('#trainModel').click();const report=await (await response).json();
  await page.waitForFunction(()=>!document.getElementById('predictModel').disabled,{timeout:120000});
  assert.equal(report.status,'trained');assert.equal(report.mode,'cross');assert.ok(report.observations>=60);
  assert.deepEqual(Object.keys(report.comparisons),['A','B','C']);
  assert.equal(await page.locator('#crossMetrics tr').count(),3);
  assert.equal(await page.locator('#crossEvidence tr').count(),report.backtest.samples);
  for(const m of Object.values(report.comparisons))for(const f of m.folds)assert.ok(Date.parse(f.max_train_label_published_at)<Date.parse(f.test_start+'T23:59:59.999+08:00'));
  assert.match(await page.locator('#trainingSummary').textContent(),/不是.*實盤績效/);
  await page.locator('#predictModel').click();await page.waitForFunction(()=>document.getElementById('labStatus').textContent==='推論完成。');
  assert.match(await page.locator('#predictionProbabilities').textContent(),/不顯示百分比/);
  fs.mkdirSync('.test-runs/cross-evidence',{recursive:true});
  await page.screenshot({path:'.test-runs/cross-evidence/real-models.png',fullPage:true});
  fs.writeFileSync('.test-runs/cross-evidence/real-backtest.json',JSON.stringify(report,null,2));
  await page.locator('#labInstrument').selectOption('fh_friday');await page.locator('#trainModel').click();
  await page.waitForFunction(()=>!document.getElementById('trainModel').disabled);
  assert.match(await page.locator('#labStatus').textContent(),/不能預測下週一/);assert.equal(await page.locator('#predictModel').isDisabled(),true);
  await page.goto(base);await page.waitForFunction(()=>document.documentElement.dataset.dashboardReady==='1'&&document.documentElement.dataset.dashboardPredictionReady==='1');
  await page.locator('#chartInstrument').selectOption('fh_rebar');await page.locator('#period').selectOption('7');
  assert.equal(await page.locator('#fhChart').getAttribute('data-points'),'1');assert.match(await page.locator('#priceTitle').textContent(),/鋼筋/);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({realObservations:report.observations,eligible:report.training_samples,models:Object.fromEntries(Object.entries(report.comparisons).map(([k,v])=>[k,{accuracy:v.accuracy,samples:v.samples,recall:v.recall,confusion_matrix:v.confusion_matrix}])),effectiveSources:report.effective_source_samples,first:report.training_start,last:report.training_end,errors}));
 }finally{await browser.close();}
});
