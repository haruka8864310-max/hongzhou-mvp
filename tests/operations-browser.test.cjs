// All operations fixtures are explicitly TEST data in an independent SQLite.
const {test}=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require('@playwright/test'),fs=require('node:fs');
const base=process.env.HONGZHOU_OPERATIONS_URL||'http://127.0.0.1:18903';
test('route-separated operations, customer key, partial shipment, profit and mobile navigation',async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],external=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*',route=>{const u=new URL(route.request().url());if(u.hostname!=='127.0.0.1'){external.push(u.href);return route.abort();}return route.continue();});
 const ready=()=>page.waitForFunction(()=>document.documentElement.dataset.operationsReady==='1');
 const fill=async(form,data)=>{for(const [key,value]of Object.entries(data))await page.locator(form+` [name="${key}"]`).fill(value);};
 const suffix=Date.now(),name='TEST browser '+suffix;
 try{
  await page.goto(base);await ready();assert.equal(await page.locator('#sideNav a[data-nav]').count(),8);assert.equal(await page.locator('#customerForm').count(),0);
  await page.locator('#sideNav a[href="/customers"]').click();await ready();await page.locator('#newCustomer').click();
  await fill('#customerForm',{name,phone:'TEST 0900',main_items:'TEST steel'});await page.locator('#customerForm button[type=submit]').click();await page.waitForFunction(()=>!document.querySelector('#customerDialog').open);
  await page.waitForFunction(()=>!document.getElementById('customerDetail').hidden);const key=(await page.locator('#detailKey').textContent()).match(/[a-f0-9]{32}/)[0];
  await page.locator('#customersBody button').filter({hasText:'編輯'}).last().click();await fill('#customerForm',{contact:'TEST contact'});await page.locator('#customerForm button[type=submit]').click();await page.waitForFunction(()=>!document.querySelector('#customerDialog').open);assert.match(await page.locator('#detailKey').textContent(),new RegExp(key));
  await page.locator('#sideNav a[href="/purchases"]').click();await ready();await page.locator('#newTrade').click();await page.locator('#tradeForm [name=customer_key]').selectOption(key);
  await fill('#tradeForm',{date:'2026-11-01',material:'TEST steel',gross_kg:'1010',deduction_kg:'10',unit_price:'10',transport:'600',processing:'400'});
  await page.locator('#tradeForm button[type=submit]').click();await page.waitForFunction(()=>!document.querySelector('#tradeDialog').open);await page.waitForFunction(()=>document.getElementById('status').textContent.includes('已儲存交易'));
  const purchase=await (await page.request.get(base+'/api/operations/trades?kind=buy&customer_key='+key)).json();const lot=purchase[0];assert.equal(lot.kg,1000);
  await page.locator('a[href="/shipments"]').first().click();await ready();await page.locator('#newTrade').click();await page.locator('#tradeForm [name=customer_key]').selectOption(key);await page.locator('#tradeForm [name=purchase_id]').selectOption(String(lot.id));
  await fill('#tradeForm',{date:'2026-11-02',gross_kg:'400',unit_price:'15',transport:'100',processing:'100'});assert.match(await page.locator('#tradePreview').textContent(),/1,400/);
  await page.locator('#tradeForm button[type=submit]').click();await page.waitForFunction(()=>!document.querySelector('#tradeDialog').open);await page.waitForFunction(()=>document.getElementById('status').textContent.includes('已儲存交易'));
  const excel=await page.request.get(base+await page.locator('#exportLink').getAttribute('href'));assert.match(excel.headers()['content-type'],/spreadsheetml/);
  await page.locator('#sideNav a[href="/profits"]').click();await ready();await page.locator('#profitForm [name=batch_key]').selectOption(lot.batch_key);await page.locator('#profitForm [name=sale_price]').fill('15');await page.locator('#profitForm [name=estimated_sale_cost]').fill('200');await page.locator('#profitForm button').click();await page.waitForFunction(()=>document.getElementById('profitCards').textContent.includes('2,200'));
  assert.match(await page.locator('#profitCards').textContent(),/1,400/);assert.match(await page.locator('#profitCards').textContent(),/6,600/);
  await page.goto(base+'/customers');await ready();await page.locator('#search').fill(name);await page.waitForTimeout(250);await page.locator('#customersBody button').filter({hasText:'查看'}).click();await page.waitForFunction(()=>!document.getElementById('customerDetail').hidden);assert.equal(await page.locator('#customerTrades tr').count(),2);
  for(const path of ['/fenghsin','/international']){await page.goto(base+path);await ready();assert.ok(Number(await page.locator('#fhChart').getAttribute('data-points'))>=1);assert.equal(await page.locator('#tradeForm').count(),0);}
  await page.goto(base+'/fenghsin');await ready();for(const product of ['fh_scrap','fh_rebar','fh_sections'])for(const period of ['7','30','90','180','365','0']){await page.locator('#chartInstrument').selectOption(product);await page.locator('#period').selectOption(period);assert.equal(await page.locator('#fhLatest').getAttribute('data-instrument'),product);}
  await page.goto(base+'/prediction-lab');await page.waitForFunction(()=>document.documentElement.dataset.labReady==='1');assert.equal(await page.locator('#trainModel').isEnabled(),true);
  await page.goto(base+'/updates');await ready();assert.equal(await page.locator('#updateRows tr').count(),8);assert.equal(external.length,0);
  await page.setViewportSize({width:375,height:812});await page.locator('#navToggle').click();assert.equal(await page.locator('#navToggle').getAttribute('aria-expanded'),'true');await page.locator('#sideNav a[href="/profits"]').click();await ready();assert.equal(await page.locator('#navToggle').getAttribute('aria-expanded'),'false');const layout=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));assert.equal(layout.width,layout.scroll);
  fs.mkdirSync('.test-runs/operations-evidence',{recursive:true});await page.screenshot({path:'.test-runs/operations-evidence/mobile-profit.png',fullPage:true});await page.setViewportSize({width:1440,height:1000});await page.screenshot({path:'.test-runs/operations-evidence/profit.png',fullPage:true});
  assert.deepEqual(errors,[]);console.log(JSON.stringify({customerKey:key,purchaseId:lot.id,batch:lot.batch_key,netKg:1000,shippedKg:400,realized:1400,unallocated:6600,estimated:2200,mobile:layout,externalRequests:external,errors}));
 }finally{await browser.close();}
});
