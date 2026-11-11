// Optional real-data acceptance. Only connect to an owned isolated localhost server.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const fs=require('node:fs');
const base=process.env.HONGZHOU_LIVE_UI_URL;
async function waitFor(fn,label){for(let i=0;i<950;i++){if(fn())return;await new Promise(r=>setTimeout(r,40));}throw Error('Acceptance timeout: '+label);}

test('real update button, reload persistence, history and empty research',{skip:!base},async()=>{
  const origin=new URL(base).origin;
  assert.ok(['127.0.0.1','localhost'].includes(new URL(base).hostname));
  const requests=[],windows=[],consoleErrors=[];
  async function load(){
    const dom=new JSDOM(await (await fetch(base+'/advanced')).text(),{url:base,runScripts:'outside-only',pretendToBeVisual:true});
    const w=dom.window,d=w.document;windows.push(w);w.AbortController=AbortController;
    let lineSegments=0;
    w.HTMLCanvasElement.prototype.getContext=()=>({clearRect(){},fillText(){},beginPath(){},moveTo(){},lineTo(){lineSegments++;},stroke(){},arc(){},fill(){}});
    w.console.error=(...args)=>consoleErrors.push(args.map(String).join(' '));
    w.addEventListener('error',e=>consoleErrors.push(e.message));
    w.fetch=async(path,opts={})=>{
      const url=new URL(path,base);assert.equal(url.origin,origin,'all requests must use the page origin');
      const entry={method:opts.method||'GET',path:url.pathname,origin:url.origin};requests.push(entry);
      const response=await fetch(url,opts);entry.status=response.status;
      if(url.pathname==='/api/market/sync')entry.result=await response.clone().json();
      return response;
    };
    const style=d.createElement('style');style.textContent=await (await fetch(new URL('/assets/market.css',base))).text();d.head.append(style);
    await w.eval(await (await fetch(new URL('/assets/market.js',base))).text());
    assert.match(d.getElementById('updated').textContent,/已連線/);
    return {w,d,segments:()=>lineSegments};
  }
  try{
    const first=await load();
    if(process.env.HONGZHOU_LIVE_UPDATE==='1'){
      const button=first.d.querySelector('[data-sync]');button.click();
      await waitFor(()=>requests.some(r=>r.path==='/api/market/sync'&&r.result),'real update response');
      const result=requests.find(r=>r.path==='/api/market/sync').result;
      assert.equal(result.ok,true,result.message);
      await waitFor(()=>!button.disabled,'button finished refreshing UI');
    }
    await waitFor(()=>first.d.getElementById('sourceSummary').textContent.includes('3 / 3'),'three currencies loaded');
    const firstRows=await (await fetch(new URL('/api/market/overview',base))).json();
    const counts=firstRows.series.filter(s=>s.latest.instrument.startsWith('fx_')).map(s=>s.count);
    assert.equal(counts.length,3);assert.ok(counts.every(c=>c>=30));
    assert.match(first.d.getElementById('cards').textContent,/中央銀行/);
    assert.match(first.d.getElementById('cards').textContent,/資料延遲|自動更新成功/);
    assert.match(first.d.getElementById('cards').textContent,/逐筆發布時間未提供/);
    assert.ok(first.d.getElementById('trendSeries').options.length>=4);
    assert.equal(first.d.getElementById('trendSeries').disabled,false);
    assert.ok(first.segments()>20,'history chart receives real line segments');
    assert.equal(first.w.getComputedStyle(first.d.querySelector('.card')).backgroundColor,'rgb(255, 255, 255)');
    first.w.close();
    const reloaded=await load(),d=reloaded.d;
    assert.match(d.getElementById('sourceSummary').textContent,/3 \/ 3/);
    assert.ok(d.getElementById('trendSeries').options.length>=4);
    const afterRows=await (await fetch(new URL('/api/market/overview',base))).json();
    assert.deepEqual(afterRows.series.filter(s=>s.latest.instrument.startsWith('fx_')).map(s=>s.count),counts);
    d.querySelector('nav [data-page="research"]').click();
    assert.equal(d.getElementById('researchSeries').disabled,true);
    assert.equal(d.getElementById('runResearch').disabled,true);
    assert.match(d.getElementById('researchResult').textContent,/沒有豐興廢鋼/);
    d.getElementById('runResearch').dispatchEvent(new reloaded.w.Event('click'));
    assert.match(d.getElementById('researchResult').textContent,/尚無豐興廢鋼/);
    assert.equal(requests.filter(r=>r.path==='/api/market/research').length,0);
    assert.doesNotMatch(d.getElementById('researchResult').textContent,/Failed to fetch/);
    assert.ok(d.getElementById('seriesA').value.includes('fx_usd'));
    assert.ok(d.getElementById('seriesB').value.includes('fx_jpy'));
    d.querySelector('nav [data-page="compare"]').click();
    d.getElementById('compareform').dispatchEvent(new reloaded.w.Event('submit',{bubbles:true,cancelable:true}));
    await waitFor(()=>d.querySelectorAll('#comparisonRows tr').length>30,'real aligned comparison');
    assert.ok(requests.every(r=>r.status===200),'all frontend HTTP requests succeeded');
    assert.deepEqual(consoleErrors,[]);
    const summary={origin,currencies:3,observations_per_currency:counts,aligned_dates:d.querySelectorAll('#comparisonRows tr').length,
      reload_persistent:true,empty_research_guard:true,console_errors:consoleErrors,requests};
    if(process.env.HONGZHOU_NETWORK_REPORT)fs.writeFileSync(process.env.HONGZHOU_NETWORK_REPORT,JSON.stringify(summary,null,2));
    console.log(JSON.stringify(summary));
  }finally{for(const w of windows)w.close();}
});
