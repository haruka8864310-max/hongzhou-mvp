// Optional real HTTP/DOM acceptance; run only against an owned isolated data directory.
const {test}=require('node:test'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom');
const base=process.env.HONGZHOU_LIVE_UI_URL;
async function wait(fn,label){for(let i=0;i<1000;i++){if(fn())return;await new Promise(r=>setTimeout(r,40));}throw Error('Timeout '+label);}
test('global market real data, countries, version entry, history, compare and failure',{skip:!base},async()=>{
 assert.ok(['127.0.0.1','localhost'].includes(new URL(base).hostname));
 const dom=new JSDOM(await(await fetch(base)).text(),{url:base,runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,errors=[];let segments=0,pending=0,offline=false;
 w.AbortController=AbortController;w.addEventListener('error',e=>errors.push(e.message));w.console.error=(...a)=>errors.push(a.map(String).join(' '));
 w.HTMLCanvasElement.prototype.getContext=()=>({clearRect(){},fillText(){},beginPath(){},moveTo(){},lineTo(){segments++;},stroke(){},arc(){},fill(){}});
 w.fetch=async(url,opts={})=>{const target=new URL(url,base);assert.equal(target.origin,new URL(base).origin);pending++;try{if(offline&&target.pathname==='/api/global/history')return new Response(JSON.stringify({detail:'TEST source unavailable'}),{status:503,headers:{'Content-Type':'application/json'}});return await fetch(target,opts);}finally{pending--;}};
 try{
  for(const name of ['market','metals','decision','detail','global'])await w.eval(await(await fetch(base+'/assets/'+name+'.js')).text());
  await wait(()=>d.documentElement.dataset.globalReady==='1','global init '+d.getElementById('globalMessage').textContent);
  assert.equal(d.querySelectorAll('nav button:not([hidden])').length,6);
  assert.ok(d.querySelectorAll('#globalSummary .card').length>=3);
  d.querySelector('[data-global-country=JP]').click();
  assert.equal(d.getElementById('countries').hidden,false);
  assert.match(d.getElementById('globalCountryRows').textContent,/H2|東京/);
  assert.match(d.getElementById('globalUnavailable').textContent,/尚未|未確認/);
  d.getElementById('globalProduct').value='jp_tokyo';d.getElementById('globalProduct').dispatchEvent(new w.Event('change'));
  assert.equal(d.getElementById('japanPlant').options.length,8);assert.equal(d.getElementById('japanGrade').options.length,8);
  assert.equal(d.querySelectorAll('#globalCountryRows .card').length,1);
  const tokyoButton=d.querySelector('#globalCountryRows [data-global-series]'),tokyoId=tokyoButton.dataset.globalSeries;tokyoButton.click();
  try{await wait(()=>d.documentElement.dataset.globalHistoryReady===tokyoId&&d.querySelectorAll('#globalHistoryRows tr').length>=2,'real Tokyo PDF history');}catch(e){throw Error(e.message+' selected='+d.getElementById('globalSeries').value+' ready='+d.documentElement.dataset.globalHistoryReady+' note='+d.getElementById('globalHistoryNote').textContent);}
  assert.match(d.getElementById('globalHistoryNote').textContent,/observed/);
  assert.match(d.getElementById('globalHistoryMetrics').textContent,/東京製鐵/);
  const overview=await(await fetch(base+'/api/global/overview')).json(),copper=overview.items.find(m=>m.id==='global_copper').series[0].id,aluminum=overview.items.find(m=>m.id==='global_aluminum').series[0].id;
  d.querySelector('nav [data-page=metals]').click();d.getElementById('globalSeries').value=copper;
  d.getElementById('globalMode').value='native';
  d.getElementById('globalMonths').value='36';d.getElementById('globalAnalyse').click();
  await wait(()=>d.querySelectorAll('#globalHistoryRows tr').length===36,'three years real copper');
  assert.ok(segments>100);assert.match(d.getElementById('globalHistoryNote').textContent,/monthly/);
  offline=true;d.getElementById('globalAnalyse').click();await wait(()=>d.getElementById('globalHistoryNote').textContent.includes('TEST source unavailable'),'failure visible');assert.equal(d.querySelectorAll('#globalHistoryRows tr').length,36);offline=false;
  d.getElementById('globalCompareA').value=copper;d.getElementById('globalCompareB').value=aluminum;d.getElementById('globalCompareRun').click();
  await wait(()=>d.getElementById('globalCompareStatus').textContent.includes('36個共同日期'),'comparison');
  d.getElementById('globalMode').value='twd';d.getElementById('globalMode').dispatchEvent(new w.Event('change'));await wait(()=>d.getElementById('globalHistoryRows').textContent.includes('TWD/t'),'historical FX conversion');
  // Only this isolated validation DB receives clearly labelled synthetic manual observations.
  d.querySelector('nav [data-page=data]').click();d.getElementById('globalInputMarket').value='jp_fob';d.getElementById('globalInputMarket').dispatchEvent(new w.Event('change'));
  d.getElementById('globalManualOpen').click();assert.ok(d.getElementById('globalPriceDialog').hasAttribute('open'));
  const f=d.getElementById('globalQuoteForm');for(const [k,v] of Object.entries({grade:'TEST ONLY H2',delivery_term:'FOB TEST port',currency:'JPY',unit:'t',frequency:'weekly',quote_date:'2025-10-01',price:'150',source:'TEST FIXTURE',source_url:'https://example.com/test',license_note:'Synthetic UI acceptance fixture'}))f.elements[k].value=v;
  f.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await wait(()=>d.getElementById('globalQuoteStatus').textContent.includes('已追加'),'manual quote saved');
  await wait(()=>pending===0,'pending fetches');assert.deepEqual(errors,[]);
  const saved=await(await fetch(base+'/api/global/settings')).json();assert.equal(saved.country,'JP');assert.equal(saved.months,36);
  console.log(JSON.stringify({visible_nav:6,countries:9,tokyo_factories:8,tokyo_grades:8,tokyo_history:true,real_history_months:36,canvas_segments:segments,manual_dialog:true,manual_saved:true,history_failure_kept_data:true,console_errors:errors}));
 }finally{await wait(()=>pending===0,'settle');w.close();}
});
