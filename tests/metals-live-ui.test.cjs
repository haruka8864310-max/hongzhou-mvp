// Optional real data DOM acceptance against an owned, isolated localhost server.
const {test}=require('node:test'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom');
const base=process.env.HONGZHOU_LIVE_UI_URL;
async function wait(fn,label){for(let i=0;i<1500;i++){if(fn())return;await new Promise(r=>setTimeout(r,40));}throw Error('Timeout: '+label);}
test('real monthly metal UI: chart, compare, leading, settings, failure isolation, reload',{skip:!base},async()=>{
 assert.ok(['127.0.0.1','localhost'].includes(new URL(base).hostname));
 const windows=[],requests=[],errors=[];let failUpdate=false;
 async function load(){
  const dom=new JSDOM(await(await fetch(base)).text(),{url:base,runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window,d=w.document;windows.push(w);w.AbortController=AbortController;
  let segments=0;w.HTMLCanvasElement.prototype.getContext=()=>({clearRect(){},fillText(){},beginPath(){},moveTo(){},lineTo(){segments++;},stroke(){},arc(){},fill(){}});
  w.addEventListener('error',e=>errors.push(e.message));w.console.error=(...v)=>errors.push(v.map(String).join(' '));
  w.fetch=async(url,opts={})=>{const target=new URL(url,base);assert.equal(target.origin,new URL(base).origin);requests.push(target.pathname);if(failUpdate&&target.pathname==='/api/metals/sync')return new Response(JSON.stringify({ok:false,results:[{provider:'worldbank',ok:false,message:'TEST ONLY simulated offline'}]}),{headers:{'Content-Type':'application/json'}});return fetch(target,opts);};
  await w.eval(await(await fetch(new URL('/assets/market.js',base))).text());
  w.eval(await(await fetch(new URL('/assets/metals.js',base))).text());
  await wait(()=>d.documentElement.dataset.metalsReady==='1'&&d.querySelectorAll('#metalCompareRows tr').length>=60,'actual monthly comparison');
  return {w,d,segments:()=>segments};
 }
 try{
  const first=await load(),d=first.d;
  assert.equal(d.getElementById('metals').hidden,false);
  assert.match(d.getElementById('metalMetrics').textContent,/World Bank 銅/);
  assert.match(d.getElementById('metalMetrics').textContent,/3個月趨勢/);
  assert.ok(first.segments()>100);
  assert.ok(d.getElementById('metalA').options.length>=6);
  d.getElementById('updateMetals').click();
  await wait(()=>!d.getElementById('updateMetals').disabled,'real update button');
  assert.match(d.getElementById('metalSourceRows').textContent,/World Bank/);
  d.getElementById('runLeading').click();
  await wait(()=>d.querySelectorAll('#leadingRows tr').length===4,'lag0-3 results');
  assert.match(d.getElementById('leadingLimitations').textContent,/相關性不代表因果/);
  d.getElementById('metalMonths').value='60';d.getElementById('metalTrendMode').value='fx_index';
  d.getElementById('metalSettings').dispatchEvent(new first.w.Event('submit',{bubbles:true,cancelable:true}));
  await wait(()=>d.getElementById('metalStatus').textContent.includes('參數已保存'),'saved settings');
  const saved=await(await fetch(new URL('/api/metals/settings',base))).json();assert.equal(saved.months,60);assert.equal(saved.trend_mode,'fx_index');
  assert.equal(d.querySelectorAll('#metalCompareRows tr').length,60);
  assert.match(d.getElementById('monthlyRadar').textContent,/銅價|World Bank|BLS/);
  const count=d.querySelectorAll('#metalCompareRows tr').length;failUpdate=true;d.getElementById('updateMetals').click();
  await wait(()=>!d.getElementById('updateMetals').disabled,'offline update');
  assert.match(d.getElementById('metalStatus').textContent,/simulated offline/);
  assert.equal(d.querySelectorAll('#metalCompareRows tr').length,count);
  assert.ok(d.querySelectorAll('#metalMetrics .card').length>=5);
  first.w.close();failUpdate=false;
  const reload=await load();assert.equal(reload.d.getElementById('metalMonths').value,'60');assert.equal(reload.d.getElementById('metalTrendMode').value,'fx_index');
  assert.equal(reload.d.querySelectorAll('#metalCompareRows tr').length,60);
  reload.d.querySelector('nav [data-page="research"]').click();
  assert.match(reload.d.getElementById('researchResult').textContent,/沒有豐興/);
  assert.equal(reload.d.getElementById('runResearch').disabled,true);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({comparison_months:60,leading_lags:4,price_chart_segments:first.segments(),persisted_after_reload:true,failure_kept_chart:true,console_errors:errors,api_requests:requests.length}));
 }finally{windows.forEach(w=>w.close());}
});
