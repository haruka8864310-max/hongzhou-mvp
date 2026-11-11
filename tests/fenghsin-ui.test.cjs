// Real HTTP and downloaded numeric facts only. No fixtures are written by this test.
const {test}=require('node:test'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom');
const base=process.env.HONGZHOU_LIVE_UI_URL;
async function wait(fn,label){for(let i=0;i<600;i++){if(fn())return;await new Promise(r=>setTimeout(r,50));}throw Error('Timeout '+label);}
test('Feng Hsin downloaded history, all products, periods, source links, update and failure',{skip:!base},async()=>{
 assert.ok(['127.0.0.1','localhost'].includes(new URL(base).hostname));
 const dom=new JSDOM(await(await fetch(base+'/advanced')).text(),{url:base,runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,errors=[];let lines=0,bars=0,offline=false,pending=0;
 w.AbortController=AbortController;w.addEventListener('error',e=>errors.push(e.message));w.console.error=(...a)=>errors.push(a.map(String).join(' '));
 w.HTMLCanvasElement.prototype.getContext=()=>({clearRect(){},fillText(){},beginPath(){},moveTo(){},lineTo(){lines++;},stroke(){},arc(){},fill(){},fillRect(){bars++;}});
 w.fetch=async(url,opts={})=>{const target=new URL(url,base);assert.equal(target.origin,new URL(base).origin);pending++;try{if(offline&&target.pathname.startsWith('/api/fenghsin'))throw new TypeError('Failed to fetch');return await fetch(target,opts);}finally{pending--;}};
 try{
  for(const name of ['market','metals','decision','detail','global','fenghsin'])w.eval(await(await fetch(base+'/assets/'+name+'.js')).text());
  await wait(()=>d.getElementById('fhChart').dataset.points,'initial real chart');
  assert.equal(d.querySelectorAll('#fhCards button').length,3);
  assert.equal(d.querySelectorAll('nav button:not([hidden])').length,5);
  const counts={};
  for(const [i,product] of ['scrap','rebar','sections'].entries()){
   d.querySelectorAll('#fhCards button')[i].click();
   await wait(()=>d.getElementById('fhChartNote').textContent.includes(['廢鋼收購價','鋼筋基價','型鋼基價'][i]),'product '+product);
   counts[product]=Number(d.getElementById('fhChart').dataset.points);assert.ok(counts[product]>=2);
  }
  for(const period of ['3','6','12','36','0']){
   d.getElementById('fhPeriod').value=period;d.getElementById('fhPeriod').dispatchEvent(new w.Event('change'));
   await wait(()=>pending===0,'period '+period);assert.equal(d.getElementById('fhChart').hidden,false);
   const expected=await(await fetch(base+'/api/fenghsin/history?product=sections&months='+period)).json();
   assert.equal(Number(d.getElementById('fhChart').dataset.points),expected.points.length);
  }
  assert.ok(lines>10&&bars>0);
  assert.ok([...d.querySelectorAll('#fhRows a')].every(a=>a.href.startsWith('https://juyi-steel.tw/')||a.href.startsWith('https://www.double-steel.com/')));
  d.getElementById('fhUpdate').click();await wait(()=>!d.getElementById('fhUpdate').disabled,'one click update');
  assert.match(d.getElementById('fhMessage').textContent,/讀到|冷卻期間/);
  const rowCount=d.querySelectorAll('#fhRows tr').length,plotted=d.getElementById('fhChart').dataset.points;
  offline=true;d.getElementById('fhUpdate').click();await wait(()=>!d.getElementById('fhUpdate').disabled,'update failure');
  assert.match(d.getElementById('fhMessage').textContent,/更新失敗/);
  assert.equal(d.querySelectorAll('#fhRows tr').length,rowCount);assert.equal(d.getElementById('fhChart').dataset.points,plotted);
  assert.equal(w.localStorage.getItem('fhProduct'),'sections');assert.equal(w.localStorage.getItem('fhPeriod'),'0');
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({real_history_points:counts,source_links:true,line_segments:lines,change_bars:bars,periods:['3','6','12','36','0'],update_button:true,failure_preserves_history:true,console_errors:errors}));
 }finally{await wait(()=>pending===0,'settle');w.close();}
});
