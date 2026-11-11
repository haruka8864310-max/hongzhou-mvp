// DOM integration against an actual FastAPI process and disposable SQLite DB.
// Synthetic fixtures only; this is not a visual browser test.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'..');

async function waitFor(fn,label){for(let i=0;i<150;i++){if(await fn())return;await new Promise(r=>setTimeout(r,40));}throw Error('Timed out: '+label);}

test('market frontend integrated with FastAPI',async t=>{
  const dataDir=fs.mkdtempSync(path.join(root,'.test-runs','dom-ui-'));
  const port=18879,base=`http://127.0.0.1:${port}`;
  const python=process.env.HONGZHOU_TEST_PYTHON||path.join(root,'.venv','Scripts','python.exe');
  const server=spawn(python,['-m','uvicorn','app:app','--host','127.0.0.1','--port',String(port)],{cwd:root,env:{...process.env,HONGZHOU_DATA_DIR:dataDir,HONGZHOU_DISABLE_SCHEDULER:'1'},windowsHide:true,stdio:['ignore','pipe','pipe']});
  let logs='';server.stderr.on('data',b=>logs+=b.toString());
  let dom;
  try{
    await waitFor(async()=>{try{return (await fetch(base+'/api/market/catalog')).ok;}catch{return false;}},'server '+logs);
    dom=new JSDOM(await (await fetch(base+'/advanced')).text(),{url:base,runScripts:'outside-only',pretendToBeVisual:true});
    const w=dom.window,d=w.document;
    w.AbortController=AbortController;
    const errors=[];w.addEventListener('error',e=>errors.push(e.message));
    // jsdom has no raster renderer; exercise chart data flow with a no-op context.
    w.HTMLCanvasElement.prototype.getContext=()=>({clearRect(){},fillText(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},arc(){},fill(){}});
    w.fetch=async(url,opts={})=>{
      if(opts.body instanceof w.FormData){const native=new FormData();for(const [k,v] of opts.body){if(typeof v==='string')native.append(k,v);else{const bytes=await new Promise((resolve,reject)=>{const r=new w.FileReader();r.onload=()=>resolve(r.result);r.onerror=reject;r.readAsArrayBuffer(v);});native.append(k,new Blob([bytes],{type:v.type}),v.name);}}opts={...opts,body:native};}
      return fetch(new URL(url,base),opts);
    };
    w.eval(fs.readFileSync(path.join(root,'assets','market.js'),'utf8'));
    await waitFor(()=>d.querySelectorAll('#cards .card').length===11&&d.getElementById('updated').textContent.includes('已連線'),'empty market cards');
    await t.test('empty state, navigation and legal source guide',async()=>{
      assert.match(d.getElementById('cards').textContent,/尚無資料/);
      assert.equal(d.getElementById('trendSeries').disabled,true);
      assert.match(d.getElementById('trendSeries').textContent,/尚無已取得/);
      assert.equal(d.getElementById('researchSeries').disabled,true);
      assert.equal(d.getElementById('runResearch').disabled,true);
      assert.match(d.getElementById('researchResult').textContent,/沒有豐興廢鋼/);
      for(const id of ['compare','research','radar','data','overview']){d.querySelector(`nav [data-page="${id}"]`).click();assert.equal(d.getElementById(id).hidden,false);assert.equal([...d.querySelectorAll('main > section')].filter(x=>!x.hidden).length,1);}
      assert.equal((await fetch(base+'/market-guide')).status,200);
    });
    await t.test('manual quote form persists and refreshes overview',async()=>{
      const form=d.getElementById('quoteform');
      for(const [day,price] of [['2025-01-01','100'],['2025-01-08','108']]){
        const values={instrument:'fh_scrap',date:day,price,currency:'TWD',unit:'t',specification:'TEST ONLY',contract:'TEST',source:'TEST FIXTURE',source_url:'https://example.com/test',license_note:'Synthetic test fixture',published_at:day+'T16:00',delay:'unknown'};
        for(const [key,value] of Object.entries(values))form.elements[key].value=value;
        form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
        await waitFor(()=>!form.querySelector('button').disabled,'quote submit');
        assert.match(d.getElementById('quoteStatus').textContent,/已儲存/);
      }
      assert.match(d.getElementById('cards').textContent,/108/);
      assert.match(d.getElementById('cards').textContent,/異常提示/);
    });
    await t.test('CSV file form imports second market',async()=>{
      const metadata=await (await fetch(base+'/api/market/catalog')).json();
      const lines=[metadata.csv_fields.join(',')];
      for(const [day,price] of [['2025-01-01',200],['2025-01-08',220]])lines.push(['jp_h2',day,price,'JPY','t','TEST H2','TEST','TEST FIXTURE','https://example.com/test','Synthetic test fixture','unknown',day+'T16:00:00+08:00'].join(','));
      const file=new w.File([lines.join('\n')],'test-only.csv',{type:'text/csv'});
      Object.defineProperty(d.getElementById('csvfile'),'files',{value:[file],configurable:true});
      const form=d.getElementById('csvform');form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
      await waitFor(()=>!form.querySelector('button').disabled,'CSV submit');
      assert.match(d.getElementById('csvStatus').textContent,/已匯入 2 筆/);
    });
    await t.test('comparison renders aligned history and missing-FX state',async()=>{
      const a=d.getElementById('seriesA'),b=d.getElementById('seriesB');
      a.value=[...a.options].find(x=>x.value.includes('fh_scrap')).value;
      b.value=[...b.options].find(x=>x.value.includes('jp_h2')).value;
      d.getElementById('compareStart').value='2025-01-01';d.getElementById('compareEnd').value='2025-01-31';
      const form=d.getElementById('compareform');form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
      await waitFor(()=>!form.querySelector('button').disabled,'comparison');
      assert.equal(d.querySelectorAll('#comparisonRows tr').length,2);
      assert.match(d.getElementById('comparisonRows').textContent,/108/);
      assert.match(d.getElementById('correlation').textContent,/無法可靠估計/);
      d.getElementById('compareMode').value='twd';form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
      await waitFor(()=>!form.querySelector('button').disabled,'missing FX');
      assert.equal(d.querySelectorAll('#comparisonRows tr').length,0);
      assert.match(d.getElementById('comparisonStatus').textContent,/資料不足/);
    });
    await t.test('research and radar show honest insufficient-data states',async()=>{
      const select=d.getElementById('researchSeries');select.selectedIndex=1;d.getElementById('runResearch').click();
      await waitFor(()=>d.getElementById('researchResult').textContent.includes('目前 2 筆'),'research result');
      assert.match(d.getElementById('researchResult').textContent,/無法可靠預測/);
      d.getElementById('radarform').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
      await waitFor(()=>d.getElementById('radarResult').textContent.includes('沒有符合'),'radar result');
      assert.match(d.getElementById('radarResult').textContent,/不代表沒有風險/);
      assert.deepEqual(errors,[]);
    });
    async function diagnosticWindow(url,handler,raw=false){
      const instance=new JSDOM(raw?fs.readFileSync(path.join(root,'market.html'),'utf8'):await (await fetch(base+'/advanced')).text(),{url,runScripts:'outside-only'});
      const win=instance.window;win.AbortController=AbortController;
      win.HTMLCanvasElement.prototype.getContext=()=>({clearRect(){},fillText(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},arc(){},fill(){}});
      const consoleErrors=[];win.console.error=(...args)=>consoleErrors.push(args.map(String).join(' '));
      win.fetch=handler;
      await win.eval(fs.readFileSync(path.join(root,'assets','market.js'),'utf8'));
      return {win,doc:win.document,consoleErrors};
    }
    await t.test('direct HTML opening gives actionable diagnosis without a fetch or crash',async()=>{
      let calls=0;
      const {win,doc}=await diagnosticWindow('file:///C:/test/market.html',async()=>{calls++;throw Error('must not fetch');},true);
      try{assert.equal(calls,0);assert.match(doc.getElementById('globalStatus').textContent,/直接開啟.*HTML/);assert.match(doc.getElementById('globalStatus').textContent,/start_windows.bat/);assert.equal(doc.getElementById('runResearch').disabled,true);assert.equal(doc.getElementById('retryConnection').hidden,false);}finally{win.close();}
    });
    await t.test('wrong static server or stale backend does not leave blank controls',async()=>{
      const {win,doc,consoleErrors}=await diagnosticWindow('http://127.0.0.1:5500/market.html',async()=>new Response('Not Found',{status:404}),true);
      try{assert.match(doc.getElementById('globalStatus').textContent,/HTTP 404/);assert.match(doc.getElementById('cards').textContent,/服務尚未連線/);assert.match(doc.getElementById('researchSeries').textContent,/本機服務未連線/);assert.ok(consoleErrors.length>0);}finally{win.close();}
    });
    await t.test('connection refusal reports the exact same-origin API and records console cause',async()=>{
      const {win,doc,consoleErrors}=await diagnosticWindow(base+'/',async(url)=>{if(new URL(url).pathname==='/api/health')return fetch(url);throw new TypeError('Failed to fetch');});
      try{assert.match(doc.getElementById('globalStatus').textContent,/無法連線至本機服務/);assert.match(doc.getElementById('globalStatus').textContent,/\/api\/market\/catalog/);assert.doesNotMatch(doc.getElementById('globalStatus').textContent,/Failed to fetch/);assert.ok(consoleErrors.some(x=>x.includes('Failed to fetch')));}finally{win.close();}
    });
    await t.test('source-status failure does not prevent overview rendering',async()=>{
      const {win,doc}=await diagnosticWindow(base+'/',async(url,opts)=>new URL(url).pathname==='/api/market/sources'?new Response(JSON.stringify({detail:'TEST source status failure'}),{status:503,headers:{'Content-Type':'application/json'}}):fetch(url,opts));
      try{assert.ok(doc.querySelectorAll('#cards .card').length>0);assert.match(doc.getElementById('globalStatus').textContent,/來源狀態讀取失敗/);}finally{win.close();}
    });
    await t.test('settings form persists selection, frequency, period and thresholds',async()=>{
      d.querySelector('nav [data-page="data"]').click();
      for(const input of d.querySelectorAll('#trackedMarkets input'))input.checked=['fx_usd','fx_jpy'].includes(input.value);
      d.getElementById('defaultCompareA').value='fx_jpy';d.getElementById('defaultCompareB').value='fx_usd';
      d.getElementById('analysisDays').value='90';d.getElementById('anomalyPercent').value='2.5';
      d.getElementById('updateMinutes').value='60';d.getElementById('autoUpdate').checked=false;
      const form=d.getElementById('settingsform');form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
      await waitFor(()=>!form.querySelector('button').disabled,'save settings');
      assert.match(d.getElementById('settingsStatus').textContent,/設定已保存/);
      const saved=await (await fetch(base+'/api/market/settings')).json();
      assert.deepEqual(saved.tracked_markets,['fx_usd','fx_jpy']);
      assert.deepEqual(saved.compare_markets,['fx_jpy','fx_usd']);
      assert.equal(saved.update_minutes,60);assert.equal(saved.analysis_days,90);assert.equal(saved.anomaly_percent,2.5);
      assert.equal(saved.auto_update,false);assert.equal(d.getElementById('overviewThreshold').value,'2.5');
      assert.equal(d.querySelectorAll('#cards .card').length,2);
      assert.match(d.getElementById('sourceSummaryNote').textContent,/背景更新已停用/);
      assert.match(d.getElementById('sourceRows').textContent,/需要手動輸入/);
      assert.match(d.getElementById('sourceRows').textContent,/等待首次自動更新/);
      assert.deepEqual(errors,[]);
    });
  }finally{
    dom?.window.close();server.kill();
    await new Promise(resolve=>{if(server.exitCode!==null)resolve();else server.once('exit',resolve);});
  }
});
