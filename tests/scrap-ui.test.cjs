// Read real SQLite through HTTP; never create price fixtures in this acceptance.
const {test}=require('node:test'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom');
const base=process.env.HONGZHOU_LIVE_UI_URL;
async function wait(fn,label){for(let i=0;i<600;i++){if(fn())return;await new Promise(r=>setTimeout(r,50));}throw Error('Timeout '+label);}
test('real Feng Hsin Tokyo curves, native/index/TWD, periods, source dates and failure',{skip:!base},async()=>{
 const health=await(await fetch(base+'/api/health')).json();assert.match(health.data_directory,/\.test-runs|\.ui-test-data/,'UI mutation tests require an isolated DB');
 const initialCatalog=await(await fetch(base+'/api/scrap-analysis/catalog')).json();
 const oldNonScrap=initialCatalog.items.find(s=>s.market==='tw_sections');
 if(oldNonScrap)await fetch(base+'/api/scrap-analysis/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({a:oldNonScrap.id,b:initialCatalog.defaults.b,mode:'index',months:12,max_age_days:35})});
 const dom=new JSDOM(await(await fetch(base)).text(),{url:base,runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,errors=[],apiCalls=[];let lines=0,pending=0,offline=false;
 w.AbortController=AbortController;w.addEventListener('error',e=>errors.push(e.message));w.console.error=(...a)=>errors.push(a.map(String).join(' '));
 w.HTMLCanvasElement.prototype.getContext=()=>({clearRect(){},fillText(){},beginPath(){},moveTo(){},lineTo(){lines++;},stroke(){},arc(){},fill(){},fillRect(){}});
 w.fetch=async(url,opts={})=>{const target=new URL(url,base);assert.equal(target.origin,new URL(base).origin);apiCalls.push(target.pathname);pending++;try{if(offline&&target.pathname==='/api/scrap-analysis/compare')throw new TypeError('Failed to fetch');return await fetch(target,opts);}finally{pending--;}};
 try{
  for(const name of ['market','metals','decision','detail','global','fenghsin','scrap-analysis'])w.eval(await(await fetch(base+'/assets/'+name+'.js')).text());
  try{await wait(()=>d.documentElement.dataset.scrapReady==='1'&&d.documentElement.dataset.scrapResearchReady==='1','automatic comparison and prediction');}catch(e){throw Error(e.message+' | '+d.getElementById('scrapStatus').textContent+' | '+d.getElementById('scrapResearchStatus').textContent+' | errors '+JSON.stringify(errors)+' | calls '+JSON.stringify(apiCalls));}
  assert.match(d.getElementById('scrapA').selectedOptions[0].text,/豐興廢鋼/);
  assert.match(d.getElementById('scrapB').selectedOptions[0].text,/東京製鐵.*岡山工場.*二級/);
  const counts={};
  for(const mode of ['native','index','twd']){
   d.getElementById('scrapMode').value=mode;d.getElementById('scrapMode').dispatchEvent(new w.Event('change'));
   await wait(()=>d.getElementById('scrapChart').dataset.mode===mode&&pending===0,'mode '+mode);
   counts[mode]={a:Number(d.getElementById('scrapChart').dataset.aPoints),b:Number(d.getElementById('scrapChart').dataset.bPoints)};
   assert.ok(counts[mode].a>=2&&counts[mode].b>=2);
  }
  assert.ok(lines>100);assert.ok(apiCalls.includes('/api/scrap-analysis/predict'));assert.equal(d.querySelectorAll('#scrapPerformance tr').length,5);
  assert.ok(d.querySelectorAll('#scrapAligned a').length>2);
  d.querySelector('nav [data-page=compare]').click();assert.equal(d.getElementById('scrapPanel').parentElement.id,'scrapCompareHost');
  d.querySelector('nav [data-page=overview]').click();assert.equal(d.getElementById('scrapPanel').parentElement.id,'scrapHomeHost');
  assert.equal(d.getElementById('fhChart').hidden,false);
  offline=true;const previous=d.getElementById('scrapChart').dataset.segments;d.getElementById('scrapRun').click();
  await wait(()=>d.getElementById('scrapStatus').textContent.includes('比較失敗'),'failure notice');
  assert.equal(d.getElementById('scrapChart').dataset.segments,previous);offline=false;
  if(process.env.HONGZHOU_RESEARCH_UI==='1'){
   d.getElementById('scrapResearchRun').click();await wait(()=>d.documentElement.dataset.scrapResearchReady==='1','research');
   assert.equal(d.querySelectorAll('#scrapLeading tr').length,5);
   assert.equal(d.querySelectorAll('#scrapForecast article').length,4);
   assert.match(d.getElementById('scrapForecast').textContent,/尚無可靠預測/);
   d.querySelector('nav [data-page=research]').click();assert.equal(d.getElementById('scrapResearch').parentElement.id,'scrapResearchHost');
   await wait(()=>!d.getElementById('scrapResearchRun').disabled,'research navigation');
   d.querySelector('nav [data-page=overview]').click();assert.equal(d.getElementById('scrapResearch').parentElement.id,'scrapResearchHomeHost');
  }
  await wait(()=>pending===0,'settle');assert.deepEqual(errors,[]);
  console.log(JSON.stringify({sqlite_history_points:counts,canvas_segments:lines,source_links:true,homepage_and_analysis:true,failure_keeps_chart:true,research:process.env.HONGZHOU_RESEARCH_UI==='1',prediction_api:'/api/scrap-analysis/predict',automatic_prediction:true,console_errors:errors}));
 }finally{await wait(()=>pending===0,'settle');w.close();}
});
