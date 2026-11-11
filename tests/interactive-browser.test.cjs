// Real Edge rendering against a disposable copy of actual saved observations.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('@playwright/test');
const fs=require('node:fs');
const base=process.env.HONGZHOU_DASHBOARD_URL||'http://127.0.0.1:18892';
test('interactive prices on desktop and 375px touch viewport',async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const errors=[],requests=[];
 try{
  const context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
  await page.goto(base);await page.waitForFunction(()=>document.documentElement.dataset.dashboardReady==='1');
  await page.waitForFunction(()=>document.documentElement.dataset.dashboardPredictionReady==='1');
  const sw=await page.evaluate(async()=>{const r=await navigator.serviceWorker.ready;return {scope:r.scope,state:r.active.state,caches:await caches.keys()};});
  assert.equal(sw.state,'activated');assert.equal(sw.scope,base+'/');assert.deepEqual(sw.caches,[]);
  const manifest=await (await context.request.get(base+'/assets/manifest.webmanifest')).json();
  assert.deepEqual(manifest.icons.map(x=>x.sizes),['192x192','512x512']);
  assert.equal(await page.locator('main section').count(),5);
  assert.equal(await page.locator('form').count(),0);
  assert.equal(await page.locator('#marketRows tr').count(),3);
  assert.equal(await page.locator('#fhChart').getAttribute('data-points'),'21');
  assert.ok(await page.locator('#fhChart canvas').count()>0);
  assert.equal(await page.locator('#chartInstrument option').count(),6);
  const chart=await page.locator('#fhChart').boundingBox();
  for(let x=40;x<chart.width-90;x+=30){await page.mouse.move(chart.x+x,chart.y+130);if((await page.locator('#chartTooltip').textContent()).includes('較前筆'))break;}
  const tooltip=await page.locator('#chartTooltip').textContent();
  assert.match(tooltip,/2026-.*較前筆/);
  await page.mouse.wheel(0,-200);await page.mouse.down();await page.mouse.move(chart.x+250,chart.y+130);await page.mouse.up();
  await page.locator('#chartInstrument').selectOption({label:'日本東京製鐵岡山／二級'});
  await page.locator('#period').selectOption('0');
  assert.equal(await page.locator('#fhChart').getAttribute('data-points'),'75');
  await page.locator('#chartInstrument').selectOption('fh_scrap');
  for(const period of ['7','30','90','180','365','0']){await page.locator('#period').selectOption(period);assert.match(await page.locator('#chartNote').textContent(),/實際觀察值/);}
  await page.locator('#period').selectOption('180');
  fs.mkdirSync('.test-runs/browser-evidence',{recursive:true});
  await page.screenshot({path:'.test-runs/browser-evidence/desktop.png',fullPage:true});
  const first=await page.locator('#fhChart').getAttribute('data-points');
  await page.route('**/api/dashboard/update',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'TEST source failure'})}));
  await page.locator('#updateAll').click();await page.waitForFunction(()=>!document.getElementById('updateAll').disabled);
  assert.equal(await page.locator('#fhChart').getAttribute('data-points'),first);
  assert.match(await page.locator('#updateMessage').textContent(),/TEST source failure/);
  const mobile=await browser.newContext({viewport:{width:375,height:812},isMobile:true,hasTouch:true,deviceScaleFactor:1}),phone=await mobile.newPage();
  phone.on('pageerror',e=>errors.push(e.message));
  await phone.goto(base);await phone.waitForFunction(()=>document.documentElement.dataset.dashboardReady==='1');
  const layout=await phone.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,nav:getComputedStyle(document.querySelector('nav')).position,chart:document.getElementById('fhChart').clientWidth}));
  assert.ok(layout.scroll<=375,JSON.stringify(layout));assert.equal(layout.nav,'fixed');assert.ok(layout.chart>200&&layout.chart<375);
  await phone.locator('#chartInstrument').selectOption('fh_rebar');
  await phone.locator('#period').selectOption('90');
  await phone.locator('#fhChart').scrollIntoViewIfNeeded();
  const touchChart=await phone.locator('#fhChart').boundingBox();
  const cdp=await mobile.newCDPSession(phone);
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:touchChart.x+touchChart.width/2,y:touchChart.y+120}]});
  await phone.waitForTimeout(600);
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  const touchTooltip=await phone.locator('#chartTooltip').textContent();assert.match(touchTooltip,/2026-/);
  await phone.screenshot({path:'.test-runs/browser-evidence/mobile-375.png',fullPage:true});
  assert.ok(requests.some(u=>u.includes('/api/scrap-analysis/predict')));
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({tooltip,touchTooltip,layout,chartLibrary:'Lightweight Charts 5.3.0',realMarketRows:3,predictionApi:true,errors}));
 }finally{await browser.close();}
});
