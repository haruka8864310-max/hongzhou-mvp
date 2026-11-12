'use strict';
(async()=>{
const $=id=>document.getElementById(id),page=document.querySelector('main').dataset.page,fmt=n=>Number(n).toLocaleString('zh-TW',{maximumFractionDigits:3}),p=(root,s)=>{const n=document.createElement('p');n.textContent=s;root.append(n);return n;};
async function api(path){const r=await fetch('/api/'+path);const d=await r.json();if(!r.ok)throw Error(d.detail||'本機資料讀取失敗');return d;}
try{
 if(page==='analysis'){
  const d=await api('dashboard?months=0');p($('analysisFactors'),d.direction.headline);
  for(const [key,label] of [['up','支持上漲'],['down','支持下跌'],['mixed','矛盾訊號']])for(const f of d.direction[key]){p($('analysisFactors'),label+'：'+f.title+'／'+f.reason);if(f.url){const a=document.createElement('a');a.textContent=(f.date||'')+' 原始來源';a.href=f.url;a.target='_blank';a.rel='noopener noreferrer';$('analysisFactors').append(a);}}
  for(const w of d.direction.warnings)p($('analysisFactors'),w);
  const r=await api('scrap-analysis/predict');for(const f of r.forecasts)p($('analysisForecast'),`${f.weeks}週：合格訓練${f.training_samples}筆／測試${f.backtest_samples}筆；${f.reliable&&f.calibration?.passed?'已有研究回測，請於實驗室查看限制':'尚無可靠預測，不顯示機率'}`);
 }else{
  $('period').onchange=()=>HongzhouChart.period();$('chartInstrument').onchange=()=>{HongzhouChart.select();adjust();};$('showAllHistory').onclick=()=>HongzhouChart.all();
  const data=await api('dashboard/chart');data.series=data.series.filter(s=>page==='fenghsin'?s.id.startsWith('fh_'):!s.id.startsWith('fh_'));HongzhouChart.render(data);
  function adjust(){if(!$('adjustments'))return;const s=data.series.find(s=>s.id===$('chartInstrument').value),root=$('adjustments');root.replaceChildren();if(!s)return;const changes=s.points.slice(1).map((x,i)=>({date:x.date,delta:x.price-s.points[i].price,gap:(Date.parse(x.date)-Date.parse(s.points[i].date))/86400000}));p(root,`保存${s.points.length}個價格日期；相鄰價格上漲${changes.filter(x=>x.delta>0).length}次、持平${changes.filter(x=>!x.delta).length}次、下跌${changes.filter(x=>x.delta<0).length}次。`);for(const x of changes.slice(-12).reverse())p(root,`${x.date}：${x.delta>=0?'+':''}${fmt(x.delta)}元/公噸${x.gap>8?'（跨公告缺口，僅為相鄰已知價差）':''}`);}
  adjust();
  if(page==='international'){const d=await api('dashboard?months=0');for(const m of d.markets){const root=$('internationalRows'),a=document.createElement('a');p(root,`${m.label}：${fmt(m.latest.price)} ${m.currency}/${m.unit}／報價${m.last_date}／${m.count}筆（${m.first_date}～${m.last_date}）／${m.stale?'已過期':'依原頻率延遲資料'}／最近變化${m.change==null?'無前期':fmt(m.change)}`);a.href=m.latest.source_url;a.textContent='原始來源';a.rel='noopener noreferrer';a.target='_blank';root.append(a);if(m.update?.error)p(root,'上次來源錯誤：'+m.update.error);}for(const m of d.missing)p($('internationalRows'),m.market+'：'+m.reason);}
 }
 $('status').textContent='已讀取本機SQLite歷史；本次沒有連網抓取行情。';document.documentElement.dataset.operationsReady='1';
}catch(e){$('status').textContent='讀取失敗：'+e.message+'。已顯示資料保留；請確認本機服務，或前往資料更新查看來源狀態。';}
})();
