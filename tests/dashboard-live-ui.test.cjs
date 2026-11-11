// Read-only DOM/HTTP acceptance against a running localhost server with real data.
// Canvas drawing is instrumented; this does not replace visual browser acceptance.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const base=process.env.HONGZHOU_DASHBOARD_URL||'http://127.0.0.1:18891';
async function until(fn){for(let i=0;i<600;i++){if(fn())return;await new Promise(r=>setTimeout(r,50));}throw Error('UI timeout');}
test('decision homepage renders real saved observations and retains them on failure',async()=>{
  const data=await (await fetch(base+'/api/dashboard?months=0')).json();
  assert.ok(data.fenghsin.total_count>=21);
  assert.equal(data.markets.length,3);
  assert.ok(data.news.items.length>=6);
  const dom=new JSDOM(await (await fetch(base)).text(),{url:base,runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window,d=w.document,requests=[],errors=[];
  let offline=false,newsOffline=false,draws=0;
  w.AbortController=AbortController;
  w.HTMLCanvasElement.prototype.getContext=()=>({clearRect(){},fillText(){},beginPath(){},moveTo(){},lineTo(){draws++;},stroke(){},arc(){},fill(){}});
  // Contract stub only; interactive-browser.test.cjs exercises the real library.
  w.LightweightCharts={LineSeries:{},CrosshairMode:{Normal:0},createChart:()=>({subscribeCrosshairMove(){},addSeries(){return {setData(){draws++;}};},removeSeries(){},timeScale(){return {fitContent(){}};}})};
  w.addEventListener('error',e=>errors.push(e.message));
  w.fetch=(url,opts)=>{requests.push(String(url));if(offline&&String(url).includes('dashboard?'))throw Error('Failed to fetch');if(offline&&String(url).includes('dashboard/update'))return Promise.resolve(new Response(JSON.stringify({results:[]})));if(newsOffline&&String(url).includes('news/update'))return Promise.resolve(new Response(JSON.stringify({detail:'TEST HTTP503'}),{status:503}));return fetch(new URL(url,base),opts);};
  try{
    w.eval(fs.readFileSync(path.join(__dirname,'..','assets','market-chart.js'),'utf8'));
    w.eval(fs.readFileSync(path.join(__dirname,'..','assets','dashboard.js'),'utf8'));
    try{await until(()=>d.documentElement.dataset.dashboardReady==='1'&&d.documentElement.dataset.dashboardPredictionReady==='1');}
    catch(e){throw Error(e.message+' '+d.getElementById('connectionStatus').textContent+' '+d.getElementById('forecastNote').textContent+' '+errors.join(';'));}
    assert.equal(d.querySelectorAll('main section').length,5);
    assert.equal(d.querySelectorAll('form').length,0);
    assert.equal(d.querySelectorAll('canvas').length,0);
    assert.equal(d.getElementById('fhChart').hidden,false);
    assert.ok(Number(d.getElementById('fhChart').dataset.points)>=2&&draws>0);
    assert.equal(d.querySelectorAll('#marketRows tr').length,3);
    assert.equal(d.querySelectorAll('#performanceRows tr').length,4);
    assert.equal(d.querySelectorAll('#newsItems article').length,data.news.items.length);
    assert.ok(requests.includes('/api/scrap-analysis/predict'));
    assert.match(d.getElementById('marketRows').textContent,/3167\.3|3,167\.3/);
    assert.ok(d.querySelector('#marketRows a[href^="https://www.stats.gov.cn/"]'));
    assert.match(d.getElementById('forecastNote').textContent,/尚無合格回測/);
    if(process.env.HONGZHOU_LIVE_UPDATE==='1'){
      d.getElementById('updateAll').click();
      for(let i=0;i<2400&&d.getElementById('updateAll').disabled;i++)await new Promise(r=>setTimeout(r,100));
      assert.equal(d.getElementById('updateAll').disabled,false);
      assert.ok(requests.includes('/api/dashboard/update'));
      assert.equal(d.getElementById('fhChart').hidden,false);
      assert.equal(d.querySelectorAll('#marketRows tr').length,3);
      console.log('Real update button: '+d.getElementById('updateMessage').textContent);
    }
    for(const period of ['90','180','365','0']){
      d.getElementById('period').value=period;
      d.getElementById('period').dispatchEvent(new w.Event('change'));
      assert.ok(Number(d.getElementById('fhChart').dataset.points)>=2);
    }
    const chart=d.getElementById('fhChart').dataset.points,news=d.getElementById('newsItems').textContent;
    offline=true;d.getElementById('updateAll').click();
    await until(()=>d.getElementById('connectionStatus').textContent.includes('讀取失敗'));
    await until(()=>!d.getElementById('updateAll').disabled);
    assert.equal(d.getElementById('fhChart').dataset.points,chart);
    newsOffline=true;d.getElementById('newsUpdate').click();
    await until(()=>!d.getElementById('newsUpdate').disabled);
    assert.match(d.getElementById('newsStatus').textContent,/HTTP503/);
    assert.equal(d.getElementById('newsItems').textContent,news);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({fhPoints:chart,markets:data.markets.map(m=>({label:m.label,count:m.count,first:m.first_date,last:m.last_date})),news:data.news.items.length,predictionApiCalled:true,errors}));
  }finally{dom.window.close();}
});
