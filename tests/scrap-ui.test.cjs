// Read real SQLite through HTTP; never create price fixtures in this acceptance.
const {test}=require('node:test'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom');
const base=process.env.HONGZHOU_LIVE_UI_URL;
async function wait(fn,label){for(let i=0;i<600;i++){if(fn())return;await new Promise(r=>setTimeout(r,50));}throw Error('Timeout '+label);}
test('real Feng Hsin Tokyo curves, native/index/TWD, periods, source dates and failure',{skip:!base},async()=>{
 const dom=new JSDOM(await(await fetch(base)).text(),{url:base,runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,errors=[];let lines=0,pending=0,offline=false;
 w.AbortController=AbortController;w.addEventListener('error',e=>errors.push(e.message));w.console.error=(...a)=>errors.push(a.map(String).join(' '));
 w.HTMLCanvasElement.prototype.getContext=()=>({clearRect(){},fillText(){},beginPath(){},moveTo(){},lineTo(){lines++;},stroke(){},arc(){},fill(){},fillRect(){}});
 w.fetch=async(url,opts={})=>{const target=new URL(url,base);assert.equal(target.origin,new URL(base).origin);pending++;try{if(offline&&target.pathname==='/api/scrap-analysis/compare')throw new TypeError('Failed to fetch');return await fetch(target,opts);}finally{pending--;}};
 try{
  for(const name of ['market','metals','decision','detail','global','fenghsin','scrap-analysis'])w.eval(await(await fetch(base+'/assets/'+name+'.js')).text());
  await wait(()=>d.documentElement.dataset.scrapReady==='1','initial compare');
  assert.match(d.getElementById('scrapA').selectedOptions[0].text,/豐興廢鋼/);
  assert.match(d.getElementById('scrapB').selectedOptions[0].text,/東京製鐵.*岡山工場.*二級/);
  const counts={};
  for(const mode of ['native','index','twd']){
   d.getElementById('scrapMode').value=mode;d.getElementById('scrapMode').dispatchEvent(new w.Event('change'));
   await wait(()=>d.getElementById('scrapChart').dataset.mode===mode&&pending===0,'mode '+mode);
   counts[mode]={a:Number(d.getElementById('scrapChart').dataset.aPoints),b:Number(d.getElementById('scrapChart').dataset.bPoints)};
   assert.ok(counts[mode].a>=2&&counts[mode].b>=2);
  }
  assert.ok(lines>100);
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
  }
  await wait(()=>pending===0,'settle');assert.deepEqual(errors,[]);
  console.log(JSON.stringify({sqlite_history_points:counts,canvas_segments:lines,source_links:true,homepage_and_analysis:true,failure_keeps_chart:true,research:process.env.HONGZHOU_RESEARCH_UI==='1',console_errors:errors}));
 }finally{await wait(()=>pending===0,'settle');w.close();}
});
