// Optional smoke check against a server using isolated, genuinely downloaded rates.
// Set HONGZHOU_LIVE_UI_URL to an owned localhost test server; no synthetic quotes.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const base=process.env.HONGZHOU_LIVE_UI_URL;
test('real-rate dashboard, light theme and comparison DOM',{skip:!base},async()=>{
  const url=new URL(base);
  assert.ok(['127.0.0.1','localhost'].includes(url.hostname));
  const dom=new JSDOM(await (await fetch(base)).text(),{url:base,runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window,d=w.document;
  try{
    const css=await (await fetch(new URL('/assets/market.css',base))).text();
    const style=d.createElement('style');style.textContent=css;d.head.append(style);
    w.HTMLCanvasElement.prototype.getContext=()=>({clearRect(){},fillText(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},arc(){},fill(){}});
    w.fetch=(path,opts)=>fetch(new URL(path,base),opts);
    w.eval(await (await fetch(new URL('/assets/market.js',base))).text());
    async function waitFor(fn){for(let i=0;i<150;i++){if(fn())return;await new Promise(r=>setTimeout(r,40));}throw Error('DOM did not finish loading real data');}
    await waitFor(()=>d.getElementById('sourceSummary').textContent.includes('3 / 3'));
    assert.match(d.getElementById('cards').textContent,/中央銀行/);
    assert.match(d.getElementById('cards').textContent,/資料延遲|自動更新成功/);
    assert.match(d.getElementById('cards').textContent,/逐筆發布時間未提供/);
    await waitFor(()=>/\d+ 筆/.test(d.getElementById('trendStatus').textContent));
    assert.doesNotMatch(d.getElementById('trendStatus').textContent,/0 筆/);
    assert.equal(w.getComputedStyle(d.querySelector('.card')).backgroundColor,'rgb(255, 255, 255)');
    assert.equal(d.getElementById('seriesA').value.includes('fx_usd'),true);
    assert.equal(d.getElementById('seriesB').value.includes('fx_jpy'),true);
    d.querySelector('nav [data-page="compare"]').click();
    d.getElementById('compareform').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
    await waitFor(()=>d.querySelectorAll('#comparisonRows tr').length>30);
    assert.doesNotMatch(d.getElementById('comparisonStatus').textContent,/資料不足/);
  }finally{w.close();}
});
