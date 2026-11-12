// Opt-in live public source test, always against an independent data directory.
const {test}=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require('@playwright/test');
test('explicit China update button persists source result and retains historical cache',{skip:process.env.HONGZHOU_LIVE_UPDATE!=='1'},async()=>{
 const base=process.env.HONGZHOU_OPERATIONS_URL||'http://127.0.0.1:18903',browser=await chromium.launch({channel:'msedge',headless:true}),page=await browser.newPage();
 try{
  const old=await (await page.request.get(base+'/api/dashboard/chart')).json();
  await page.goto(base+'/updates');await page.waitForFunction(()=>document.documentElement.dataset.operationsReady==='1');
  const requested=page.waitForResponse(r=>r.url().endsWith('/api/updates/china')&&r.request().method()==='POST');
  await page.locator('#updateRows tr').filter({hasText:'中國官方旬度鋼價'}).getByRole('button',{name:'更新資料'}).click();
  assert.equal((await requested).status(),200);
  let result;const deadline=Date.now()+150000;
  do{result=(await(await page.request.get(base+'/api/updates')).json()).sources.find(s=>s.source==='china');if(['success','failed','cached'].includes(result.state))break;await new Promise(resolve=>setTimeout(resolve,300));}while(Date.now()<deadline);
  const after=await(await page.request.get(base+'/api/dashboard/chart')).json();
  for(const s of old.series){const n=after.series.find(x=>x.id===s.id);assert.ok(n&&n.points.length>=s.points.length);}
  assert.ok(['success','failed','cached'].includes(result.state));console.log(JSON.stringify({source:result.source,state:result.state,added:result.added,total:result.total,successAt:result.success_at,error:result.error,detail:JSON.parse(result.detail||'{}')}));
 }finally{await browser.close();}
});
