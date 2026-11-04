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
  const server=spawn(python,['-m','uvicorn','app:app','--host','127.0.0.1','--port',String(port)],{cwd:root,env:{...process.env,HONGZHOU_DATA_DIR:dataDir},windowsHide:true,stdio:['ignore','pipe','pipe']});
  let logs='';server.stderr.on('data',b=>logs+=b.toString());
  let dom;
  try{
    await waitFor(async()=>{try{return (await fetch(base+'/api/market/catalog')).ok;}catch{return false;}},'server '+logs);
    dom=new JSDOM(await (await fetch(base+'/')).text(),{url:base,runScripts:'outside-only',pretendToBeVisual:true});
    const w=dom.window,d=w.document;
    const errors=[];w.addEventListener('error',e=>errors.push(e.message));
    // jsdom has no raster renderer; exercise chart data flow with a no-op context.
    w.HTMLCanvasElement.prototype.getContext=()=>({clearRect(){},fillText(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},arc(){},fill(){}});
    w.fetch=async(url,opts={})=>{
      if(opts.body instanceof w.FormData){const native=new FormData();for(const [k,v] of opts.body){if(typeof v==='string')native.append(k,v);else{const bytes=await new Promise((resolve,reject)=>{const r=new w.FileReader();r.onload=()=>resolve(r.result);r.onerror=reject;r.readAsArrayBuffer(v);});native.append(k,new Blob([bytes],{type:v.type}),v.name);}}opts={...opts,body:native};}
      return fetch(new URL(url,base),opts);
    };
    w.eval(fs.readFileSync(path.join(root,'assets','market.js'),'utf8'));
    await waitFor(()=>d.querySelectorAll('#cards .card').length===11,'empty market cards');
    await t.test('empty state, navigation and legal source guide',async()=>{
      assert.match(d.getElementById('cards').textContent,/尚無資料/);
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
  }finally{
    dom?.window.close();server.kill();
    await new Promise(resolve=>{if(server.exitCode!==null)resolve();else server.once('exit',resolve);});
  }
});
